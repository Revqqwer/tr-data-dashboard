# -*- coding: utf-8 -*-
"""
Mağaza uygulamasına (iOS/Android) özel API — web sitesi bunları kullanmaz.

  GET  /api/m/quotes?s=BIST:XU100,FX_IDC:USDTRY     → anlık fiyat + günlük değişim (TradingView)
  GET  /api/m/search?q=thy                          → hisse arama (BIST + ABD)
  GET  /api/m/funds/search?q=tly                    → TEFAS fon arama
  GET  /api/m/funds/summary?codes=TLY,PHE           → fon fiyatı, getiriler, son para akışı
  POST /api/m/watch                                 → cihazın takip listesini kaydet (bildirimler için)

Uygulama yerel dosyadan (capacitor://localhost) çalıştığı için bu uçlar CORS'a açıktır.
"""
import datetime as _dt
import json
import sqlite3
import time
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

mobile_bp = Blueprint('mobile_api', __name__)

_ROOT = Path(__file__).resolve().parent
DB_PATH = str(_ROOT / 'data' / 'cache.db')
APP_ORIGINS = {'capacitor://localhost', 'ionic://localhost', 'http://localhost', 'https://localhost'}

_TV_SCAN = 'https://scanner.tradingview.com/global/scan'
_TV_SEARCH = 'https://symbol-search.tradingview.com/symbol_search/v3/'
_TV_HEADERS = {'Origin': 'https://www.tradingview.com', 'Referer': 'https://www.tradingview.com/',
               'User-Agent': 'Mozilla/5.0'}
_QCACHE: dict = {}          # sembol → (ts, veri)
_QTTL = 60


@mobile_bp.after_app_request
def _cors(resp):
    """Yalnızca uygulama kökeninden gelen /api/ isteklerine CORS izni."""
    origin = request.headers.get('Origin', '')
    if origin in APP_ORIGINS and request.path.startswith('/api/'):
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Vary'] = 'Origin'
    return resp


@mobile_bp.route('/api/m/<path:_any>', methods=['OPTIONS'])
def _preflight(_any):
    return ('', 204)


# ── Fiyatlar ────────────────────────────────────────────────────────────────
def quotes(symbols: list) -> dict:
    now = time.time()
    need = [s for s in symbols if s not in _QCACHE or now - _QCACHE[s][0] > _QTTL]
    if need:
        try:
            r = requests.post(_TV_SCAN, headers=_TV_HEADERS, timeout=8, json={
                'symbols': {'tickers': need, 'query': {'types': []}},
                'columns': ['close', 'change', 'description', 'currency', 'change_abs']})
            for it in r.json().get('data', []):
                d = it.get('d') or []
                if d and d[0] is not None:
                    _QCACHE[it['s']] = (now, {'symbol': it['s'], 'price': d[0], 'change': d[1],
                                              'name': d[2], 'currency': d[3], 'change_abs': d[4]})
        except Exception:
            pass
    return {s: _QCACHE[s][1] for s in symbols if s in _QCACHE}


@mobile_bp.route('/api/m/quotes')
def api_quotes():
    syms = [s.strip().upper() for s in (request.args.get('s') or '').split(',') if s.strip()][:60]
    q = quotes(syms)
    return jsonify([q[s] for s in syms if s in q])


@mobile_bp.route('/api/m/search')
def api_search():
    text = (request.args.get('q') or '').strip()
    if len(text) < 1:
        return jsonify([])
    out = []
    try:
        r = requests.get(_TV_SEARCH, headers=_TV_HEADERS, timeout=8,
                         params={'text': text, 'search_type': 'stocks', 'lang': 'tr', 'domain': 'production'})
        for s in r.json().get('symbols', [])[:40]:
            ex = (s.get('exchange') or '').upper()
            if ex not in ('BIST', 'NASDAQ', 'NYSE', 'AMEX'):
                continue
            sym = (s.get('symbol') or '').replace('<em>', '').replace('</em>', '')
            out.append({'symbol': f'{ex}:{sym}', 'ticker': sym, 'exchange': ex,
                        'name': (s.get('description') or '').replace('<em>', '').replace('</em>', '')})
    except Exception:
        pass
    # BIST sonuçları önde
    out.sort(key=lambda x: 0 if x['exchange'] == 'BIST' else 1)
    return jsonify(out[:15])


# ── TEFAS fonları ───────────────────────────────────────────────────────────
def _tefas():
    from tefas_backend.database import engine, FundMeta, FundDaily, FundFlow
    from sqlmodel import Session, select
    return engine, FundMeta, FundDaily, FundFlow, Session, select


@mobile_bp.route('/api/m/funds/search')
def api_fund_search():
    q = (request.args.get('q') or '').strip().upper()
    if len(q) < 2:
        return jsonify([])
    engine, FundMeta, _, _, Session, select = _tefas()
    with Session(engine) as db:
        rows = db.exec(select(FundMeta)).all()
    hits = [{'code': r.code, 'name': r.fname, 'type': r.fund_type} for r in rows
            if q in (r.code or '') or q in (r.fname or '').upper()]
    hits.sort(key=lambda x: (0 if x['code'].startswith(q) else 1, x['code']))
    return jsonify(hits[:20])


def fund_summary(codes: list) -> list:
    engine, FundMeta, FundDaily, FundFlow, Session, select = _tefas()
    out = []
    with Session(engine) as db:
        for code in codes:
            meta = db.get(FundMeta, code)
            prices = db.exec(select(FundDaily.trade_date, FundDaily.price, FundDaily.aum, FundDaily.investors)
                             .where(FundDaily.code == code).where(FundDaily.price > 0)
                             .order_by(FundDaily.trade_date.desc()).limit(260)).all()
            flows = db.exec(select(FundFlow.trade_date, FundFlow.net_flow, FundFlow.flow_pct)
                            .where(FundFlow.code == code)
                            .order_by(FundFlow.trade_date.desc()).limit(22)).all()
            if not prices:
                continue
            last = prices[0]

            def ret(n):
                if len(prices) > n and prices[n].price and last.price:
                    return (last.price / prices[n].price - 1) * 100
                return None

            out.append({
                'code': code, 'name': meta.fname if meta else code,
                'date': last.trade_date.isoformat(), 'price': last.price, 'aum': last.aum,
                'investors': last.investors,
                'ret_1d': ret(1), 'ret_1w': ret(5), 'ret_1m': ret(21), 'ret_1y': ret(250),
                'flow_1d': flows[0].net_flow if flows else None,
                'flow_pct_1d': flows[0].flow_pct if flows else None,
                'flow_1w': sum(f.net_flow or 0 for f in flows[:5]) if flows else None,
                'flow_1m': sum(f.net_flow or 0 for f in flows[:21]) if flows else None,
                'spark': [p.price for p in reversed(prices[:30]) if p.price],
            })
    return out


@mobile_bp.route('/api/m/funds/summary')
def api_fund_summary():
    codes = [c.strip().upper() for c in (request.args.get('codes') or '').split(',') if c.strip()][:30]
    return jsonify(fund_summary(codes))


# ── Cihaz takip listesi (bildirimler) ───────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS mobile_watch (
            token      TEXT PRIMARY KEY,
            platform   TEXT,
            stocks     TEXT,      -- JSON: [{"symbol":"BIST:THYAO","above":null,"below":null}]
            funds      TEXT,      -- JSON: ["TLY","PHE"]
            prefs      TEXT,      -- JSON: {"move_pct":3,"fund_flow":true,"fund_return":true,"briefs":true}
            last_sent  TEXT,      -- JSON: {"key": "YYYY-MM-DD"} tekrar bildirimi önler
            updated_at TEXT)''')


@mobile_bp.route('/api/m/watch', methods=['POST'])
def api_watch():
    d = request.get_json(silent=True) or {}
    token = (d.get('token') or '').strip()
    if not token or len(token) > 400:
        return jsonify({'ok': False}), 400
    stocks = [s for s in (d.get('stocks') or []) if isinstance(s, dict) and s.get('symbol')][:50]
    funds = [str(f).upper()[:8] for f in (d.get('funds') or [])][:30]
    prefs = d.get('prefs') if isinstance(d.get('prefs'), dict) else {}
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT INTO mobile_watch (token,platform,stocks,funds,prefs,last_sent,updated_at) '
                  'VALUES (?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET platform=excluded.platform, '
                  'stocks=excluded.stocks, funds=excluded.funds, prefs=excluded.prefs, updated_at=excluded.updated_at',
                  (token, d.get('platform') or 'ios', json.dumps(stocks), json.dumps(funds),
                   json.dumps(prefs), '{}', _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return jsonify({'ok': True})
