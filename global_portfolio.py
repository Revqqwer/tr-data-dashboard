"""Global Hisse Portföyü — admin panelinden girilen pozisyonlar + TradingView canlı fiyat.

Adet tutulmaz: her pozisyon yalnızca giriş fiyatı ile izlenir, getiri = fiyat / giriş - 1.
Fiyatlar TradingView scanner'ından toplu çekilir (tek HTTP isteği) ve 5 dk bellekte tutulur.
"""
import sqlite3, time
from datetime import date

import requests

_TV_SCAN = 'https://scanner.tradingview.com/global/scan'
_TV_HEADERS = {'Content-Type': 'application/json', 'Origin': 'https://www.tradingview.com',
               'Referer': 'https://www.tradingview.com/', 'User-Agent': 'Mozilla/5.0'}
_US_EXCHANGES = ('NASDAQ', 'NYSE', 'AMEX')          # borsa yazılmazsa bu sırayla denenir
_CACHE_TTL = 300
_price_cache: dict = {}                              # {'EXCH:SYM': (ts, {...})}


def init(db_path: str):
    with sqlite3.connect(db_path) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS global_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,            -- TradingView tam sembol, ör. NASDAQ:AAPL
            name TEXT, currency TEXT,
            entry_price REAL NOT NULL, entry_date TEXT NOT NULL,
            exit_price REAL, exit_date TEXT,
            note TEXT)''')


def _scan(symbols: list) -> dict:
    if not symbols:
        return {}
    r = requests.post(_TV_SCAN, headers=_TV_HEADERS, timeout=8, json={
        'symbols': {'tickers': symbols, 'query': {'types': []}},
        'columns': ['close', 'currency', 'description', 'change']})
    out = {}
    for it in r.json().get('data', []):
        d = it.get('d') or []
        if d and d[0] is not None:
            out[it['s']] = {'price': d[0], 'currency': d[1], 'name': d[2], 'change': d[3]}
    return out


def resolve(ticker: str):
    """'AAPL' → ('NASDAQ:AAPL', bilgi) · 'XETR:SAP' olduğu gibi doğrulanır. Bulunamazsa (None, None)."""
    t = ticker.strip().upper().replace(' ', '')
    cands = [t] if ':' in t else [f'{ex}:{t}' for ex in _US_EXCHANGES]
    found = _scan(cands)
    for c in cands:
        if c in found:
            return c, found[c]
    return None, None


def prices(symbols: list) -> dict:
    now = time.time()
    need = [s for s in symbols if s not in _price_cache or now - _price_cache[s][0] > _CACHE_TTL]
    if need:
        try:
            for s, v in _scan(need).items():
                _price_cache[s] = (now, v)
        except Exception:
            pass                                     # TV erişilemezse eski önbellekle devam
    return {s: _price_cache[s][1] for s in symbols if s in _price_cache}


def _rows(db_path: str):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM global_positions ORDER BY entry_date DESC, id DESC')]


def _days(a: str, b: str) -> int:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except Exception:
        return 0


def payload(db_path: str) -> dict:
    rows = _rows(db_path)
    live = prices([r['symbol'] for r in rows if r['exit_price'] is None])
    today = date.today().isoformat()
    open_, closed = [], []
    for r in rows:
        tk = r['symbol'].split(':')[-1]
        base = {'id': r['id'], 'ticker': tk, 'symbol': r['symbol'], 'name': r['name'],
                'currency': r['currency'], 'entry_price': r['entry_price'],
                'entry_date': r['entry_date'], 'note': r['note']}
        if r['exit_price'] is None:
            p = live.get(r['symbol'])
            price = p['price'] if p else None
            open_.append({**base, 'price': price, 'day_change': p['change'] if p else None,
                          'ret': (price / r['entry_price'] - 1) * 100 if price else None,
                          'days': _days(r['entry_date'], today)})
        else:
            closed.append({**base, 'exit_price': r['exit_price'], 'exit_date': r['exit_date'],
                           'ret': (r['exit_price'] / r['entry_price'] - 1) * 100,
                           'days': _days(r['entry_date'], r['exit_date'])})
    closed.sort(key=lambda x: x['exit_date'] or '', reverse=True)

    def avg(xs):
        xs = [x['ret'] for x in xs if x['ret'] is not None]
        return sum(xs) / len(xs) if xs else None
    return {'open': open_, 'closed': closed, 'updated': time.strftime('%Y-%m-%d %H:%M'),
            'summary': {'open_count': len(open_), 'open_avg': avg(open_),
                        'closed_count': len(closed), 'closed_avg': avg(closed),
                        'win_rate': (sum(1 for c in closed if c['ret'] > 0) / len(closed) * 100) if closed else None}}


# ── Admin işlemleri ──────────────────────────────────────────────────────────
def add(db_path: str, ticker: str, entry_price: float, entry_date: str, note: str = ''):
    sym, info = resolve(ticker)
    if not sym:
        raise ValueError(f'"{ticker}" TradingView\'de bulunamadı. Borsa ile yazmayı deneyin (ör. XETR:SAP).')
    with sqlite3.connect(db_path) as conn:
        conn.execute('INSERT INTO global_positions (symbol,name,currency,entry_price,entry_date,note) VALUES (?,?,?,?,?,?)',
                     (sym, info['name'], info['currency'], entry_price, entry_date, note))
    return sym


def close(db_path: str, pid: int, exit_price: float = None, exit_date: str = None):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute('SELECT symbol FROM global_positions WHERE id=?', (pid,)).fetchone()
        if not row:
            raise ValueError('Pozisyon bulunamadı.')
        if not exit_price:                           # boşsa güncel fiyattan kapat
            p = prices([row[0]]).get(row[0])
            if not p:
                raise ValueError('Güncel fiyat alınamadı; çıkış fiyatını elle yazın.')
            exit_price = p['price']
        conn.execute('UPDATE global_positions SET exit_price=?, exit_date=? WHERE id=?',
                     (exit_price, exit_date or date.today().isoformat(), pid))


def reopen(db_path: str, pid: int):
    with sqlite3.connect(db_path) as conn:
        conn.execute('UPDATE global_positions SET exit_price=NULL, exit_date=NULL WHERE id=?', (pid,))


def delete(db_path: str, pid: int):
    with sqlite3.connect(db_path) as conn:
        conn.execute('DELETE FROM global_positions WHERE id=?', (pid,))
