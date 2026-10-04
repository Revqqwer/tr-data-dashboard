# -*- coding: utf-8 -*-
"""
TradingView bağlantısı — kullanıcı hesabına bağlı iki özellik:

  1) Alarm köprüsü: her üyeye özel webhook adresi. TradingView'da alarm kurarken
     "Webhook URL" alanına yapıştırılır; alarm tetiklenince 3N Finans'a düşer,
     geçmişe yazılır ve üyenin cihazlarına (web push + iOS/Android) bildirim gider.
  2) Takip listesi aktarımı: TradingView → "Listeyi dışa aktar" ile inen .txt dosyası
     (EXCHANGE:SYMBOL virgüllü, ###BÖLÜM başlıklı) yüklenir, üyenin listesine eklenir.

  GET  /tradingview                 → sayfa (grafik + alarm + liste)
  GET  /api/tv/me                   → webhook adresi, son alarmlar, takip listesi
  POST /api/tv/rotate               → webhook adresini yenile (eskisi geçersiz olur)
  POST /api/tv/import               → .txt içeriği (form 'file' veya 'text')
  POST /api/tv/watch/remove         → {symbol}
  POST /api/tv/hook/<token>         → TradingView webhook (herkese açık, token ile)

TradingView hesabına doğrudan bağlanmak mümkün değil (resmi OAuth/API yok);
şifre istenmez, kullanıcı verisi TradingView'dan okunmaz.
"""
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, session

tv_bp = Blueprint('tv_link', __name__)

DB_PATH = str(Path(__file__).resolve().parent / 'data' / 'cache.db')
SITE = 'https://www.3nfinans.com'
_SYM_RE = re.compile(r'^[A-Z0-9_.!]{1,20}:[A-Z0-9_.!/-]{1,30}$')
_HOOK_LIMIT = 60              # token başına saatte en fazla alarm
_hook_hits: dict = {}         # token → [zaman damgaları]


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS tv_hooks (
            username   TEXT PRIMARY KEY,
            token      TEXT UNIQUE NOT NULL,
            created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS tv_alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            message    TEXT,
            created_at TEXT)''')
        c.execute('CREATE INDEX IF NOT EXISTS ix_tv_alerts_user ON tv_alerts(username, id)')
        c.execute('''CREATE TABLE IF NOT EXISTS user_watch (
            username TEXT NOT NULL,
            symbol   TEXT NOT NULL,
            added_at TEXT,
            PRIMARY KEY (username, symbol))''')


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _me():
    return session.get('username') if session.get('logged_in') else None


def _token_for(username: str, rotate: bool = False) -> str:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute('SELECT token FROM tv_hooks WHERE username=?', (username,)).fetchone()
        if row and not rotate:
            return row[0]
        tok = secrets.token_urlsafe(24)
        c.execute('INSERT INTO tv_hooks (username,token,created_at) VALUES (?,?,?) '
                  'ON CONFLICT(username) DO UPDATE SET token=excluded.token, created_at=excluded.created_at',
                  (username, tok, _now()))
        return tok


def delete_user(username: str):
    """Hesap silinirken çağrılır."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        for t in ('tv_hooks', 'tv_alerts', 'user_watch'):
            c.execute(f'DELETE FROM {t} WHERE username=?', (username,))


def parse_watchlist(text: str) -> list:
    """TradingView dışa aktarımı: 'BIST:THYAO,NASDAQ:AAPL,###BÖLÜM,...' (satır/virgül)."""
    out = []
    for part in re.split(r'[,\n\r]+', text or ''):
        s = part.strip().upper()
        if not s or s.startswith('###'):
            continue
        if _SYM_RE.match(s) and s not in out:
            out.append(s)
    return out[:300]


def _notify_user(username: str, title: str, body: str):
    """Yalnızca bu üyenin cihazlarına: web push abonelikleri + hesaba bağlı iOS/Android token'ları."""
    try:
        import push
        if push.VAPID_PRIVATE_KEY and push.VAPID_PUBLIC_KEY:
            from pywebpush import webpush, WebPushException
            payload = json.dumps({'title': title, 'body': body, 'url': '/tradingview',
                                  'tag': 'tradingview', 'icon': push.DEFAULT_ICON}, ensure_ascii=False)
            for sub in push.list_subscriptions():
                if sub.get('username') != username:
                    continue
                try:
                    webpush(subscription_info={'endpoint': sub['endpoint'], 'keys': sub['keys']},
                            data=payload, vapid_private_key=push.VAPID_PRIVATE_KEY,
                            vapid_claims={'sub': push.VAPID_SUBJECT}, timeout=15)
                except WebPushException as e:
                    if getattr(getattr(e, 'response', None), 'status_code', None) in (404, 410):
                        push.delete_subscription(sub['endpoint'])
                except Exception:
                    pass
    except Exception:
        pass
    try:
        import apns, fcm
        apns.init_db()
        with sqlite3.connect(apns.DB_PATH) as c:
            rows = c.execute('SELECT token, platform FROM native_push_tokens WHERE username=?',
                             (username,)).fetchall()
        ios = [t for t, p in rows if p == 'ios']
        android = [t for t, p in rows if p == 'android']
        if ios and apns.configured():
            apns.send(title, body, url='/tradingview', tag='tradingview', tokens=ios)
        if android and fcm.configured():
            fcm.send(title, body, url='/tradingview', tag='tradingview', tokens=android)
    except Exception:
        pass


# ── Sayfa ve üye API'leri ───────────────────────────────────────────────────
@tv_bp.route('/tradingview')
def page():
    return render_template('tradingview.html', logged_in=bool(_me()), username=_me() or '')


@tv_bp.route('/api/tv/me')
def api_me():
    me = _me()
    if not me:
        return jsonify({'error': 'unauthorized'}), 401
    tok = _token_for(me)
    with sqlite3.connect(DB_PATH) as c:
        alerts = [{'message': m, 'at': a} for m, a in c.execute(
            'SELECT message, created_at FROM tv_alerts WHERE username=? ORDER BY id DESC LIMIT 50', (me,))]
        watch = [r[0] for r in c.execute(
            'SELECT symbol FROM user_watch WHERE username=? ORDER BY added_at, symbol', (me,))]
    return jsonify({'hook_url': f'{SITE}/api/tv/hook/{tok}', 'alerts': alerts, 'watch': watch})


@tv_bp.route('/api/tv/rotate', methods=['POST'])
def api_rotate():
    me = _me()
    if not me:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify({'hook_url': f'{SITE}/api/tv/hook/{_token_for(me, rotate=True)}'})


@tv_bp.route('/api/tv/import', methods=['POST'])
def api_import():
    me = _me()
    if not me:
        return jsonify({'error': 'unauthorized'}), 401
    f = request.files.get('file')
    text = f.read(200_000).decode('utf-8', 'ignore') if f else (request.form.get('text') or '')
    syms = parse_watchlist(text)
    if not syms:
        return jsonify({'ok': False, 'error': 'Dosyada sembol bulunamadı'}), 400
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        before = c.execute('SELECT COUNT(*) FROM user_watch WHERE username=?', (me,)).fetchone()[0]
        c.executemany('INSERT OR IGNORE INTO user_watch (username,symbol,added_at) VALUES (?,?,?)',
                      [(me, s, _now()) for s in syms])
        after = c.execute('SELECT COUNT(*) FROM user_watch WHERE username=?', (me,)).fetchone()[0]
    return jsonify({'ok': True, 'found': len(syms), 'added': after - before})


@tv_bp.route('/api/tv/watch/remove', methods=['POST'])
def api_watch_remove():
    me = _me()
    if not me:
        return jsonify({'error': 'unauthorized'}), 401
    sym = ((request.get_json(silent=True) or {}).get('symbol') or '').upper()
    with sqlite3.connect(DB_PATH) as c:
        c.execute('DELETE FROM user_watch WHERE username=? AND symbol=?', (me, sym))
    return jsonify({'ok': True})


# ── TradingView webhook (herkese açık; kimlik = gizli token) ────────────────
@tv_bp.route('/api/tv/hook/<token>', methods=['POST'])
def api_hook(token):
    if len(token) > 64:
        return ('', 404)
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute('SELECT username FROM tv_hooks WHERE token=?', (token,)).fetchone()
    if not row:
        return ('', 404)
    now = time.time()
    hits = [t for t in _hook_hits.get(token, []) if now - t < 3600]
    if len(hits) >= _HOOK_LIMIT:
        return jsonify({'error': 'rate limit'}), 429
    hits.append(now)
    _hook_hits[token] = hits

    raw = request.get_data(cache=False, as_text=True)[:2000]
    msg = raw.strip()
    try:                                   # JSON gönderildiyse okunur bir metne çevir
        d = json.loads(raw)
        if isinstance(d, dict):
            msg = d.get('message') or d.get('text') or ' · '.join(f'{k}: {v}' for k, v in list(d.items())[:8])
    except Exception:
        pass
    msg = (msg or 'TradingView alarmı')[:500]
    username = row[0]
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT INTO tv_alerts (username,message,created_at) VALUES (?,?,?)', (username, msg, _now()))
        c.execute('DELETE FROM tv_alerts WHERE username=? AND id NOT IN '
                  '(SELECT id FROM tv_alerts WHERE username=? ORDER BY id DESC LIMIT 500)', (username, username))
    _notify_user(username, 'TradingView alarmı', msg[:180])
    return jsonify({'ok': True})
