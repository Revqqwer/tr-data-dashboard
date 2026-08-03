# -*- coding: utf-8 -*-
"""
Çarkıfelek — Hisse Analizi öneri deposu.

Üyeler yayında analiz edilmesi için hisse önerir. Kurallar:
- Her üye (username) en fazla MAX_PER_USER öneri yapabilir.
- Aynı üye aynı hisseyi iki kez öneremez.
- Admin sıfırladığında tüm öneriler silinir → herkese yeniden 5 hak.

Depo: data/cache.db (push.py ile aynı SQLite dosyası).
"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
DB_PATH = str(_ROOT / 'data' / 'cache.db')

MAX_PER_USER = 5
_TICKER_RE = re.compile(r'^[A-Z0-9]{2,6}$')


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS wheel_suggestions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            name       TEXT,
            ticker     TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(username, ticker)
        )''')


def normalize_ticker(raw: str) -> str:
    """Girdiyi BIST sembolüne çevir: büyük harf, boşluk/TR karakter temizliği."""
    t = (raw or '').strip().upper()
    # Türkçe harfleri ASCII'ye indir (kullanıcı yanlışlıkla girerse)
    tr = {'İ': 'I', 'I': 'I', 'Ş': 'S', 'Ğ': 'G', 'Ü': 'U', 'Ö': 'O', 'Ç': 'C'}
    t = ''.join(tr.get(ch, ch) for ch in t)
    return re.sub(r'[^A-Z0-9]', '', t)


def user_count(username: str) -> int:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            'SELECT COUNT(*) FROM wheel_suggestions WHERE username=?',
            (username,)
        ).fetchone()[0]


def add_suggestion(username: str, name: str, raw_ticker: str) -> dict:
    """Öneri ekler. Dönen dict: {ok, remaining, error?}."""
    if not username:
        return {'ok': False, 'error': 'Giriş yapmalısın.'}
    ticker = normalize_ticker(raw_ticker)
    if not _TICKER_RE.match(ticker):
        return {'ok': False, 'error': 'Geçersiz hisse kodu (örn. THYAO).'}
    init_db()
    if user_count(username) >= MAX_PER_USER:
        return {'ok': False, 'error': f'Öneri hakkın doldu ({MAX_PER_USER}/{MAX_PER_USER}).',
                'remaining': 0}
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.execute(
                'INSERT INTO wheel_suggestions (username,name,ticker,created_at) VALUES (?,?,?,?)',
                (username, name or username, ticker,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            )
    except sqlite3.IntegrityError:
        return {'ok': False, 'error': f'{ticker} zaten senin listende.',
                'remaining': MAX_PER_USER - user_count(username)}
    return {'ok': True, 'ticker': ticker,
            'remaining': MAX_PER_USER - user_count(username)}


def remove_suggestion(username: str, raw_ticker: str) -> dict:
    """Üye kendi önerisini geri çeker."""
    ticker = normalize_ticker(raw_ticker)
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.execute('DELETE FROM wheel_suggestions WHERE username=? AND ticker=?',
                  (username, ticker))
    return {'ok': True, 'remaining': MAX_PER_USER - user_count(username)}


def list_suggestions() -> list:
    """Tüm öneriler (kim önerdi bilgisiyle), en yeni önce."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        return [
            {'ticker': r[0], 'name': r[1], 'username': r[2], 'created_at': r[3]}
            for r in c.execute(
                'SELECT ticker,name,username,created_at FROM wheel_suggestions '
                'ORDER BY created_at DESC'
            )
        ]


def unique_tickers() -> list:
    """Çarka konacak benzersiz hisse kodları (öneri sırasına göre)."""
    seen, out = set(), []
    for s in reversed(list_suggestions()):   # eskiden yeniye → stabil sıra
        if s['ticker'] not in seen:
            seen.add(s['ticker'])
            out.append(s['ticker'])
    return out


def stats() -> dict:
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        total = c.execute('SELECT COUNT(*) FROM wheel_suggestions').fetchone()[0]
        users = c.execute(
            'SELECT COUNT(DISTINCT username) FROM wheel_suggestions'
        ).fetchone()[0]
        uniq = c.execute(
            'SELECT COUNT(DISTINCT ticker) FROM wheel_suggestions'
        ).fetchone()[0]
    return {'total': total, 'users': users, 'unique': uniq}


def reset_all() -> dict:
    """Tüm önerileri sil → herkese yeniden MAX_PER_USER hak."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        n = c.execute('SELECT COUNT(*) FROM wheel_suggestions').fetchone()[0]
        c.execute('DELETE FROM wheel_suggestions')
    return {'ok': True, 'cleared': n}
