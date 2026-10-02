# -*- coding: utf-8 -*-
"""
iOS uygulaması (App Store) için Apple Push Notification service gönderimi.

Web Push (push.py) tarayıcı/PWA abonelerine gider; bu modül ise mağaza uygulamasının
cihaz token'larına APNs üzerinden gönderir. push.send_push() ikisini birlikte çağırır.

Gereksinim (PA'da bir kez):  pip3 install --user "httpx[http2]" cryptography
Anahtarlar (.env):
    APNS_KEY_ID      Apple Developer → Keys → APNs anahtarının Key ID'si
    APNS_TEAM_ID     Apple Developer hesabının Team ID'si
    APNS_KEY_PATH    indirilen AuthKey_XXXX.p8 dosyasının yolu (repo DIŞINDA tutun)
    APNS_BUNDLE_ID   uygulama paket kimliği (varsayılan com.nfinans.app)
    APNS_SANDBOX     1 → geliştirme/TestFlight öncesi sandbox sunucusu
"""
import base64
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
DB_PATH = str(_ROOT / 'data' / 'cache.db')

KEY_ID    = os.environ.get('APNS_KEY_ID', '')
TEAM_ID   = os.environ.get('APNS_TEAM_ID', '')
KEY_PATH  = os.environ.get('APNS_KEY_PATH', '')
BUNDLE_ID = os.environ.get('APNS_BUNDLE_ID', 'com.nfinans.app')
SANDBOX   = os.environ.get('APNS_SANDBOX', '0') == '1'

_jwt_cache = (0, '')          # APNs token'ı 20-60 dk arası yeniden kullanılmalı


def configured() -> bool:
    return bool(KEY_ID and TEAM_ID and KEY_PATH and os.path.exists(KEY_PATH))


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS native_push_tokens (
            token      TEXT PRIMARY KEY,
            platform   TEXT NOT NULL,
            username   TEXT,
            created_at TEXT
        )''')


def save_token(token: str, platform: str, username: str = None) -> bool:
    token = (token or '').strip()
    if not token or len(token) > 400 or platform not in ('ios', 'android'):
        return False
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT INTO native_push_tokens (token,platform,username,created_at) VALUES (?,?,?,?) '
                  'ON CONFLICT(token) DO UPDATE SET username=COALESCE(excluded.username, native_push_tokens.username)',
                  (token, platform, username, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return True


def delete_token(token: str):
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute('DELETE FROM native_push_tokens WHERE token=?', (token,))


def count() -> int:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT COUNT(*) FROM native_push_tokens WHERE platform='ios'").fetchone()[0]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _provider_token() -> str:
    """ES256 imzalı APNs sağlayıcı JWT'si (PyJWT gerektirmeden, cryptography ile)."""
    global _jwt_cache
    now = int(time.time())
    if _jwt_cache[1] and now - _jwt_cache[0] < 40 * 60:
        return _jwt_cache[1]
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    with open(KEY_PATH, 'rb') as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    head = _b64(json.dumps({'alg': 'ES256', 'kid': KEY_ID}).encode())
    body = _b64(json.dumps({'iss': TEAM_ID, 'iat': now}).encode())
    r, s = decode_dss_signature(key.sign(f'{head}.{body}'.encode(), ec.ECDSA(hashes.SHA256())))
    tok = f'{head}.{body}.{_b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}'
    _jwt_cache = (now, tok)
    return tok


def send(title: str, body: str, url: str = '/', tag: str = None, tokens: list = None) -> dict:
    """Tüm iOS cihazlarına bildirim. Geçersiz token'lar (400 BadDeviceToken / 410) silinir."""
    if not configured():
        return {'ok': False, 'error': 'APNs ayarlı değil (.env: APNS_KEY_ID/TEAM_ID/KEY_PATH)'}
    try:
        import httpx
    except ImportError:
        return {'ok': False, 'error': 'httpx kurulu değil: pip3 install --user "httpx[http2]"'}
    if tokens is None:                                # None → tüm iOS cihazları
        init_db()
        with sqlite3.connect(DB_PATH) as c:
            tokens = [r[0] for r in c.execute("SELECT token FROM native_push_tokens WHERE platform='ios'")]
    host = 'https://api.sandbox.push.apple.com' if SANDBOX else 'https://api.push.apple.com'
    payload = json.dumps({'aps': {'alert': {'title': title, 'body': body}, 'sound': 'default',
                                  'thread-id': tag or 'genel'},
                          'url': url}, ensure_ascii=False).encode()
    headers = {'authorization': f'bearer {_provider_token()}', 'apns-topic': BUNDLE_ID,
               'apns-push-type': 'alert', 'apns-priority': '10'}
    sent = failed = pruned = 0
    with httpx.Client(http2=True, timeout=15) as cl:
        for t in tokens:
            try:
                r = cl.post(f'{host}/3/device/{t}', content=payload, headers=headers)
                if r.status_code == 200:
                    sent += 1
                elif r.status_code == 410 or (r.status_code == 400 and 'BadDeviceToken' in r.text):
                    delete_token(t); pruned += 1
                else:
                    failed += 1
                    print(f'  apns hatası ({r.status_code}): {r.text[:120]}')
            except Exception as e:
                failed += 1
                print(f'  apns hatası: {str(e)[:120]}')
    return {'ok': True, 'sent': sent, 'failed': failed, 'pruned': pruned}
