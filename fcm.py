# -*- coding: utf-8 -*-
"""
Android uygulaması (Google Play) için Firebase Cloud Messaging (HTTP v1) gönderimi.

apns.py'nin Android karşılığı; token'lar aynı native_push_tokens tablosunda
platform='android' olarak durur. push.send_push() web + iOS + Android'e birlikte gönderir.

Anahtar (.env):
    FCM_SA_PATH   Firebase → Proje ayarları → Hizmet hesapları → "Yeni özel anahtar oluştur"
                  ile indirilen JSON dosyasının yolu (repo DIŞINDA tutun)
"""
import base64
import json
import os
import sqlite3
import time

import apns

SA_PATH = os.environ.get('FCM_SA_PATH', '')
_tok_cache = (0, '')          # OAuth erişim token'ı ~1 saat geçerli


def configured() -> bool:
    return bool(SA_PATH and os.path.exists(SA_PATH))


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _sa() -> dict:
    with open(SA_PATH, encoding='utf-8') as f:
        return json.load(f)


def _access_token() -> str:
    """Hizmet hesabı JWT'si (RS256, cryptography ile) → Google OAuth erişim token'ı."""
    global _tok_cache
    now = int(time.time())
    if _tok_cache[1] and now - _tok_cache[0] < 50 * 60:
        return _tok_cache[1]
    import requests
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    sa = _sa()
    key = serialization.load_pem_private_key(sa['private_key'].encode(), password=None)
    head = _b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
    body = _b64(json.dumps({'iss': sa['client_email'], 'aud': sa['token_uri'], 'iat': now, 'exp': now + 3600,
                            'scope': 'https://www.googleapis.com/auth/firebase.messaging'}).encode())
    sig = key.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    r = requests.post(sa['token_uri'], timeout=15, data={
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': f'{head}.{body}.{_b64(sig)}'})
    r.raise_for_status()
    _tok_cache = (now, r.json()['access_token'])
    return _tok_cache[1]


def send(title: str, body: str, url: str = '/', tag: str = None, tokens: list = None) -> dict:
    """Tüm Android cihazlarına bildirim. Geçersiz token'lar (404 UNREGISTERED / 400) silinir."""
    if not configured():
        return {'ok': False, 'error': 'FCM ayarlı değil (.env: FCM_SA_PATH)'}
    import requests
    if tokens is None:                                # None → tüm Android cihazları
        apns.init_db()
        with sqlite3.connect(apns.DB_PATH) as c:
            tokens = [r[0] for r in c.execute("SELECT token FROM native_push_tokens WHERE platform='android'")]
    endpoint = f"https://fcm.googleapis.com/v1/projects/{_sa()['project_id']}/messages:send"
    headers = {'Authorization': f'Bearer {_access_token()}'}
    sent = failed = pruned = 0
    for t in tokens:
        msg = {'message': {'token': t, 'notification': {'title': title, 'body': body},
                           'data': {'url': url},
                           'android': {'priority': 'high',
                                       'notification': {'tag': tag or 'genel', 'sound': 'default'}}}}
        try:
            r = requests.post(endpoint, json=msg, headers=headers, timeout=15)
            if r.status_code == 200:
                sent += 1
            elif r.status_code == 404 or (r.status_code == 400 and 'INVALID_ARGUMENT' in r.text):
                apns.delete_token(t); pruned += 1
            else:
                failed += 1
                print(f'  fcm hatası ({r.status_code}): {r.text[:120]}')
        except Exception as e:
            failed += 1
            print(f'  fcm hatası: {str(e)[:120]}')
    return {'ok': True, 'sent': sent, 'failed': failed, 'pruned': pruned}
