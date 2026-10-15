# -*- coding: utf-8 -*-
"""
İzleyici Portföyleri — üyeliksiz gönderim + yayında torbadan rastgele çekiliş.

  • /portfoy-paylas      herkese açık: takma ad + ekran görüntüsü ve/veya elle yazılmış portföy
  • /torba               yalnızca site admini: torba animasyonu, rastgele çek, çekilen torbadan çıkar
Görseller data/izleyici_uploads/ altında (gitignore), yalnızca admin görebilir.
"""
import os
import random
import sqlite3
import uuid
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_ROOT, 'data', 'cache.db')
UPLOAD_DIR = os.path.join(_ROOT, 'data', 'izleyici_uploads')

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT = 2000
MAX_NICK = 40
PER_IP_PER_DAY = 3
_MAGIC = {b'\x89PNG\r\n\x1a\n': 'png', b'\xff\xd8\xff': 'jpg', b'RIFF': 'webp'}


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS izleyici_portfoy (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nick       TEXT NOT NULL,
            text       TEXT,
            image_file TEXT,
            ip         TEXT,
            created_at TEXT,
            drawn_at   TEXT
        )''')


def _image_ext(head: bytes):
    for magic, ext in _MAGIC.items():
        if head.startswith(magic):
            if ext == 'webp' and head[8:12] != b'WEBP':
                return None
            return ext
    return None


def submit(nick: str, text: str, image_stream, ip: str) -> dict:
    """Gönderimi doğrular ve kaydeder. image_stream: dosya benzeri nesne ya da None."""
    init_db()
    nick = (nick or '').strip()[:MAX_NICK]
    text = (text or '').strip()
    if len(nick) < 2:
        return {'ok': False, 'error': 'Lütfen bir isim ya da takma ad yaz.'}
    if len(text) > MAX_TEXT:
        return {'ok': False, 'error': f'Yazı en fazla {MAX_TEXT} karakter olabilir.'}

    data = image_stream.read(MAX_IMAGE_BYTES + 1) if image_stream else b''
    if len(data) > MAX_IMAGE_BYTES:
        return {'ok': False, 'error': 'Görsel en fazla 5 MB olabilir.'}
    ext = _image_ext(data[:16]) if data else None
    if data and not ext:
        return {'ok': False, 'error': 'Sadece PNG, JPG ya da WEBP ekran görüntüsü yükleyebilirsin.'}
    if not data and not text:
        return {'ok': False, 'error': 'Bir ekran görüntüsü yükle ya da portföyünü yaz.'}

    since = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as c:
        n = c.execute('SELECT COUNT(*) FROM izleyici_portfoy WHERE ip=? AND created_at>=?', (ip, since)).fetchone()[0]
    if n >= PER_IP_PER_DAY:
        return {'ok': False, 'error': 'Bugün yeterince gönderim yaptın, yarın tekrar deneyebilirsin.'}

    fname = None
    if data:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        fname = f'{uuid.uuid4().hex}.{ext}'
        with open(os.path.join(UPLOAD_DIR, fname), 'wb') as f:
            f.write(data)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT INTO izleyici_portfoy (nick, text, image_file, ip, created_at) VALUES (?,?,?,?,?)',
                  (nick, text or None, fname, ip, now))
    return {'ok': True}


def _row(r) -> dict:
    return {'id': r[0], 'nick': r[1], 'text': r[2] or '', 'image': bool(r[3]),
            'created_at': r[4], 'drawn_at': r[5]}


def state() -> dict:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        in_bag = c.execute('SELECT COUNT(*) FROM izleyici_portfoy WHERE drawn_at IS NULL').fetchone()[0]
        drawn = c.execute('SELECT id, nick, text, image_file, created_at, drawn_at FROM izleyici_portfoy '
                          'WHERE drawn_at IS NOT NULL ORDER BY drawn_at DESC').fetchall()
    return {'in_bag': in_bag, 'drawn': [_row(r) for r in drawn]}


def draw() -> dict:
    """Torbadan rastgele bir gönderim çeker ve torbadan çıkarır."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        ids = [r[0] for r in c.execute('SELECT id FROM izleyici_portfoy WHERE drawn_at IS NULL')]
        if not ids:
            return {'ok': False, 'error': 'Torba boş.'}
        pick = random.SystemRandom().choice(ids)
        c.execute('UPDATE izleyici_portfoy SET drawn_at=? WHERE id=?',
                  (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pick))
        r = c.execute('SELECT id, nick, text, image_file, created_at, drawn_at FROM izleyici_portfoy WHERE id=?',
                      (pick,)).fetchone()
    return {'ok': True, 'item': _row(r), 'in_bag': len(ids) - 1}


def image_path(item_id: int):
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        r = c.execute('SELECT image_file FROM izleyici_portfoy WHERE id=?', (item_id,)).fetchone()
    if not r or not r[0]:
        return None
    p = os.path.join(UPLOAD_DIR, r[0])
    return p if os.path.isfile(p) else None


def reset_bag() -> int:
    """Çekilenleri torbaya geri koyar."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        return c.execute('UPDATE izleyici_portfoy SET drawn_at=NULL WHERE drawn_at IS NOT NULL').rowcount


def delete(item_id: int) -> bool:
    p = image_path(item_id)
    with sqlite3.connect(DB_PATH) as c:
        n = c.execute('DELETE FROM izleyici_portfoy WHERE id=?', (item_id,)).rowcount
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
    return n > 0


def clear_all() -> int:
    """Her şeyi siler (yeni yayın/yeni tur için)."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        files = [r[0] for r in c.execute('SELECT image_file FROM izleyici_portfoy WHERE image_file IS NOT NULL')]
        n = c.execute('DELETE FROM izleyici_portfoy').rowcount
    for f in files:
        try:
            os.remove(os.path.join(UPLOAD_DIR, f))
        except OSError:
            pass
    return n
