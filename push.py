# -*- coding: utf-8 -*-
"""
Web Push bildirimleri — abonelik deposu + gönderim.

Kimler bildirim alır: uygulamayı KURMUŞ ve bildirim İZNİ VERMİŞ kullanıcılar.
(iOS'ta ayrıca iOS 16.4+ ve ana ekrana eklenmiş olma şartı var.)

Gereksinim (PA'da bir kez):  pip3 install --user pywebpush
Anahtarlar (.env):           VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT
                             -> python gen_vapid.py ile üretilir
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
DB_PATH = str(_ROOT / 'data' / 'cache.db')

VAPID_PUBLIC_KEY  = os.environ.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT     = os.environ.get('VAPID_SUBJECT', 'mailto:hakandeveli24@gmail.com')

DEFAULT_ICON = '/static/icons/icon-192.png'


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint  TEXT UNIQUE NOT NULL,
            p256dh    TEXT NOT NULL,
            auth      TEXT NOT NULL,
            username  TEXT,
            ua        TEXT,
            created_at TEXT
        )''')


def save_subscription(sub: dict, username: str = None, ua: str = None) -> bool:
    """Tarayıcının ürettiği PushSubscription JSON'unu kaydeder (endpoint benzersiz)."""
    try:
        endpoint = sub['endpoint']
        keys = sub.get('keys') or {}
        p256dh, auth = keys['p256dh'], keys['auth']
    except (KeyError, TypeError):
        return False
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            'INSERT INTO push_subscriptions (endpoint,p256dh,auth,username,ua,created_at) '
            'VALUES (?,?,?,?,?,?) '
            'ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, '
            'username=COALESCE(excluded.username, push_subscriptions.username)',
            (endpoint, p256dh, auth, username, (ua or '')[:200],
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
    return True


def delete_subscription(endpoint: str):
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (endpoint,))


def list_subscriptions() -> list:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        return [
            {'endpoint': r[0], 'keys': {'p256dh': r[1], 'auth': r[2]}, 'username': r[3]}
            for r in c.execute('SELECT endpoint,p256dh,auth,username FROM push_subscriptions')
        ]


def count() -> int:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        return c.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0]


def send_push(title: str, body: str, url: str = '/', tag: str = None,
              icon: str = None) -> dict:
    """Tüm abonelere bildirim yollar. Ölü abonelikler (404/410) otomatik silinir."""
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return {'ok': False, 'error': 'VAPID anahtarları yok (.env → gen_vapid.py)'}
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return {'ok': False, 'error': 'pywebpush kurulu değil: pip3 install --user pywebpush'}

    payload = json.dumps({
        'title': title, 'body': body, 'url': url,
        'tag': tag or 'genel', 'icon': icon or DEFAULT_ICON,
    }, ensure_ascii=False)

    sent = failed = pruned = 0
    for sub in list_subscriptions():
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub': VAPID_SUBJECT},
                timeout=15,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status in (404, 410):        # abonelik ölmüş → temizle
                delete_subscription(sub['endpoint'])
                pruned += 1
            else:
                failed += 1
                print(f'  push hatası ({status}): {str(e)[:120]}')
        except Exception as e:
            failed += 1
            print(f'  push hatası: {str(e)[:120]}')

    return {'ok': True, 'sent': sent, 'failed': failed, 'pruned': pruned}
