# -*- coding: utf-8 -*-
"""
ABD endeks verisi kolektörü — PythonAnywhere scheduled task.

Her endeks için 2 TV isteği (günlük 1D + haftalık 1W); slice ile 6 dönem (1A/3A/6A/1Y/3Y/5Y)
+ web tarafı 1h'yi 1y'den türetir. Veri geçici DB'ye yazılır, bitince atomik swap.

Kullanım:
    cd ~/tr-data-dashboard && python collect_usa.py

PA scheduled task önerisi: her gün (BIST ile aynı mantık).
"""
import json, os, re, sqlite3, time, logging, random, string
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent
DB_PATH   = BASE_DIR / 'data' / 'usa_cache.db'
TEMP_PATH = BASE_DIR / 'data' / 'usa_cache_building.db'

# usa_api.py ile aynı liste (ad → "BORSA:SEMBOL")
from usa_api import USA_INDICES

DAILY_N      = 252
DAILY_SLICES = {'1a': 22, '3a': 65, '6a': 130, '1y': 252}
WEEKLY_N     = 260
WEEKLY_SLICES = {'3y': 156, '5y': 260}


# ── TradingView WebSocket ──────────────────────────────────────────────────────

def _rand(n=12):
    return ''.join(random.choices(string.ascii_lowercase, k=n))

def _pack(func, args):
    body = json.dumps({'m': func, 'p': args}, separators=(',', ':'))
    return f'~m~{len(body)}~m~{body}'

def _parse(raw):
    out = []
    while raw:
        m = re.match(r'^~m~(\d+)~m~', raw)
        if not m:
            break
        n = int(m.group(1)); start = m.end()
        out.append(raw[start: start + n]); raw = raw[start + n:]
    return out

def fetch_tv_ws(symbol, exchange, n_bars, resolution='1D'):
    try:
        from websocket import create_connection
    except ImportError:
        log.error('websocket-client yok: pip install --user websocket-client')
        return None, None
    try:
        ws = create_connection(
            'wss://data.tradingview.com/socket.io/websocket?from=chart%2F&date=&type=chart',
            headers={'Origin': 'https://www.tradingview.com'}, timeout=20)
        chart_sess = 'cs_' + _rand()
        sym_json = json.dumps({'adjustment': 'splits', 'symbol': f'{exchange}:{symbol}'})
        ws.send(_pack('set_auth_token', ['unauthorized_user_token']))
        ws.send(_pack('chart_create_session', [chart_sess, '']))
        ws.send(_pack('resolve_symbol', [chart_sess, 'ser_1', f'={sym_json}']))
        ws.send(_pack('create_series', [chart_sess, 's1', 's1', 'ser_1', resolution, n_bars]))
        dates, closes = [], []
        attempts = 0
        while attempts < 40:
            try:
                raw = ws.recv()
            except Exception:
                attempts += 1; continue
            for pkt in _parse(raw):
                if re.match(r'^~h~\d+$', pkt):
                    ws.send(f'~m~{len(pkt)}~m~{pkt}'); continue
                try:
                    msg = json.loads(pkt)
                except Exception:
                    continue
                if msg.get('m') == 'timescale_update':
                    bars = msg.get('p', [{}] * 2)[1].get('s1', {}).get('s', [])
                    for bar in bars:
                        v = bar.get('v', [])
                        if len(v) >= 5:
                            dt = datetime.utcfromtimestamp(v[0])
                            dates.append(dt.strftime('%Y-%m-%d'))
                            closes.append(round(float(v[4]), 2))
                    if dates:
                        ws.close(); return dates, closes
                elif msg.get('m') == 'symbol_error':
                    ws.close(); return None, None
            attempts += 1
        ws.close()
        return (dates, closes) if dates else (None, None)
    except Exception as e:
        log.warning(f'TV WS hata {exchange}:{symbol}: {e}')
        return None, None


def make_period_data(dates, closes, n):
    n = min(n, len(dates))
    d, c = dates[-n:], closes[-n:]
    base = c[0]
    if not base:
        return None
    return {
        'dates':     d,
        'values':    [round(v / base * 100, 2) for v in c],
        'lastPrice': c[-1],
        'pct':       round((c[-1] / base - 1) * 100, 2),
    }


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_history (
            name       TEXT, period TEXT, dates TEXT, values_ TEXT,
            last_price REAL, pct REAL, updated_at INTEGER,
            PRIMARY KEY (name, period)
        )""")
    conn.commit()


def save(conn, name, period, data):
    conn.execute("""
        INSERT OR REPLACE INTO index_history
        (name, period, dates, values_, last_price, pct, updated_at)
        VALUES (?,?,?,?,?,?,?)""",
        (name, period, json.dumps(data['dates']), json.dumps(data['values']),
         data['lastPrice'], data['pct'], int(time.time())))


def collect():
    try:
        import websocket  # noqa
    except ImportError:
        log.error('websocket-client kurulu değil'); return

    log.info(f'ABD kolektör başladı — {len(USA_INDICES)} endeks')
    t0 = time.time()
    if TEMP_PATH.exists():
        TEMP_PATH.unlink()
    conn = sqlite3.connect(str(TEMP_PATH))
    ok, fail = 0, []
    try:
        init_db(conn)
        total = len(USA_INDICES)
        for i, (name, sym) in enumerate(USA_INDICES.items(), 1):
            exchange, symbol = (sym.split(':', 1) + [''])[:2] if ':' in sym else ('', sym)
            if ':' not in sym:
                exchange, symbol = '', sym
            else:
                exchange, symbol = sym.split(':', 1)
            log.info(f'[{i:2d}/{total}] {name} ({sym})')
            rows = []
            d_dates, d_closes = fetch_tv_ws(symbol, exchange, DAILY_N, '1D')
            if d_dates:
                for period, n in DAILY_SLICES.items():
                    data = make_period_data(d_dates, d_closes, n)
                    if data:
                        rows.append((name, period, data))
            else:
                log.warning('         günlük veri alınamadı')
            time.sleep(0.4)
            w_dates, w_closes = fetch_tv_ws(symbol, exchange, WEEKLY_N, '1W')
            if w_dates:
                for period, n in WEEKLY_SLICES.items():
                    data = make_period_data(w_dates, w_closes, n)
                    if data:
                        rows.append((name, period, data))
            for r in rows:
                save(conn, *r)
            conn.commit()
            if rows:
                ok += 1
            else:
                fail.append(name)
            time.sleep(0.4)
    finally:
        conn.close()

    os.replace(str(TEMP_PATH), str(DB_PATH))
    elapsed = round(time.time() - t0)
    log.info(f'DB güncellendi: {DB_PATH} — {ok}/{len(USA_INDICES)} başarılı, {elapsed//60}d {elapsed%60}s')
    if fail:
        log.warning(f'Veri alınamayanlar: {", ".join(fail)}')


if __name__ == '__main__':
    collect()
