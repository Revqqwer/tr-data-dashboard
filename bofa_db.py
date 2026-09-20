# -*- coding: utf-8 -*-
"""
BofA (MLB) günlük aracı kurum dağılımı — SQLite deposu + JSON içe aktarma.

Kaynak: Fintables'ın `akd/?brokerage=MLB&start=YYYY-MM-DD&end=YYYY-MM-DD` isteğinin
JSON yanıtı. Tek günlük yanıt (start == end) ya da {"YYYY-MM-DD": yanıt, ...} biçiminde
çok günlük paket kabul edilir.

data/bofa_akd.db .gitignore'dadır: repo PUBLIC, BIST lisanslı veri git'e girmemeli.

CLI:
    python bofa_db.py import dosya.json     # içe aktar (aynı gün varsa üzerine yazar)
    python bofa_db.py status                # kaç gün / hangi aralık
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_ROOT, 'data', 'bofa_akd.db')

_SIDES = ('buy', 'sell', 'net', 'total')
_FIELDS = ('size', 'volume', 'cost', 'percentage')

SCHEMA = """
CREATE TABLE IF NOT EXISTS bofa_daily (
    trade_date TEXT NOT NULL,
    code       TEXT NOT NULL,
    buy_size   INTEGER, buy_volume   REAL, buy_cost   REAL, buy_pct   REAL,
    sell_size  INTEGER, sell_volume  REAL, sell_cost  REAL, sell_pct  REAL,
    net_size   INTEGER, net_volume   REAL, net_cost   REAL, net_pct   REAL,
    total_size INTEGER, total_volume REAL, total_cost REAL, total_pct REAL,
    PRIMARY KEY (trade_date, code)
);
CREATE INDEX IF NOT EXISTS idx_bofa_code ON bofa_daily (code, trade_date);

CREATE TABLE IF NOT EXISTS bofa_day_totals (
    trade_date        TEXT PRIMARY KEY,
    total_buy_volume  REAL,
    total_sell_volume REAL,
    row_count         INTEGER,
    imported_at       TEXT
);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def _row_values(trade_date: str, r: dict) -> tuple:
    vals = [trade_date, str(r.get('code', '')).strip().upper()]
    for side in _SIDES:
        blk = r.get(side) or {}
        for f in _FIELDS:
            vals.append(blk.get(f))
    return tuple(vals)


def _day_from_response(resp: dict, forced_date: str = None) -> str:
    """Yanıtın tarihini belirler; çok günlük aralık yanıtını (start != end) reddeder."""
    start = str(resp.get('start', ''))[:10]
    end = str(resp.get('end', ''))[:10]
    if forced_date:
        if start and end and (start != forced_date or end != forced_date):
            raise ValueError(f"{forced_date}: yanıtın aralığı {start}..{end} — tek gün değil")
        return forced_date
    if not start or start != end:
        raise ValueError(f"Tek günlük yanıt bekleniyordu (start={start!r}, end={end!r}). "
                         "Aralık yanıtı günlere ayrılamaz.")
    datetime.strptime(start, '%Y-%m-%d')
    return start


def import_response(con, resp: dict, forced_date: str = None) -> dict:
    """Tek günlük yanıtı yazar (o günün eski satırları silinip yeniden eklenir)."""
    results = resp.get('results') or []
    if not results:
        return {'date': forced_date, 'rows': 0, 'skipped': True}
    day = _day_from_response(resp, forced_date)

    rows = [_row_values(day, r) for r in results if r.get('code')]
    con.execute('DELETE FROM bofa_daily WHERE trade_date=?', (day,))
    con.executemany(
        'INSERT INTO bofa_daily VALUES (' + ','.join('?' * 18) + ')', rows)
    con.execute(
        'INSERT OR REPLACE INTO bofa_day_totals VALUES (?,?,?,?,?)',
        (day, resp.get('total_buy_volume'), resp.get('total_sell_volume'),
         len(rows), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    return {'date': day, 'rows': len(rows), 'skipped': False}


def import_file(path: str) -> list:
    """Tek yanıt ya da {tarih: yanıt} paketi içeren JSON dosyasını içe aktarır."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    con = connect()
    out = []
    try:
        if 'results' in data:                       # tek günlük yanıt
            out.append(import_response(con, data))
        else:                                       # {tarih: yanıt}
            for date_str in sorted(data):
                try:
                    out.append(import_response(con, data[date_str], forced_date=date_str))
                except ValueError as e:
                    out.append({'date': date_str, 'rows': 0, 'error': str(e)})
        con.commit()
    finally:
        con.close()
    return out


def status() -> dict:
    con = connect()
    try:
        n, first, last = con.execute(
            'SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM bofa_day_totals').fetchone()
        rows = con.execute('SELECT COUNT(*) FROM bofa_daily').fetchone()[0]
        return {'days': n, 'first': first, 'last': last, 'rows': rows}
    finally:
        con.close()


# ── Analitik sorgular ────────────────────────────────────────────────────────
def _scope(include_funds: bool) -> str:
    """Fon/ETF kodları '.F' ile biter; hisse-only görünüm için dışarıda bırakılır."""
    return '' if include_funds else " AND code NOT LIKE '%.F'"


def meta() -> dict:
    con = connect()
    try:
        first, last, n = con.execute(
            'SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM bofa_day_totals').fetchone()
        return {'first': first, 'last': last, 'days': n}
    finally:
        con.close()


def cumulative(start: str, end: str, include_funds: bool = True, code: str = None) -> dict:
    """
    Günlük net TL / net lot ve seçilen aralığın başından itibaren kümülatifleri (+ net alım).
    Tek hisse (code) için lot bazlı okuma anlamlıdır; toplamlarda alış/satış lot ve TL de döner.
    """
    q = ('SELECT trade_date, SUM(net_volume), SUM(net_size), SUM(buy_size), SUM(sell_size), '
         'SUM(buy_volume), SUM(sell_volume) FROM bofa_daily '
         'WHERE trade_date BETWEEN ? AND ?' + _scope(include_funds))
    args = [start, end]
    if code:
        q += ' AND code = ?'
        args.append(code.strip().upper())
    q += ' GROUP BY trade_date ORDER BY trade_date'
    con = connect()
    try:
        rows = con.execute(q, args).fetchall()
    finally:
        con.close()
    out, run, run_lot = [], 0.0, 0.0
    tot = {'buy_lot': 0.0, 'sell_lot': 0.0, 'buy_tl': 0.0, 'sell_tl': 0.0}
    for d, net, net_lot, bl, sl, bv, sv in rows:
        net, net_lot = net or 0.0, net_lot or 0.0
        run += net
        run_lot += net_lot
        tot['buy_lot'] += bl or 0
        tot['sell_lot'] += sl or 0
        tot['buy_tl'] += bv or 0
        tot['sell_tl'] += sv or 0
        out.append({'date': d, 'net': net, 'cum': run, 'net_lot': net_lot, 'cum_lot': run_lot})
    tot['buy_avg'] = tot['buy_tl'] / tot['buy_lot'] if tot['buy_lot'] else None
    tot['sell_avg'] = tot['sell_tl'] / tot['sell_lot'] if tot['sell_lot'] else None
    return {'series': out, 'total': run, 'total_lot': run_lot, **tot}


def top(start: str, end: str, include_funds: bool = True, limit: int = 20, by: str = 'tl') -> dict:
    """
    Aralıkta en çok net aldığı / sattığı kodlar. by='tl': net TL'ye göre, by='lot': net lota göre sıralı.
    Fiyatlar gerçek işlem fiyatlarıdır: buy_avg = Σ alış TL / Σ alış lot, sell_avg = Σ satış TL / Σ satış lot.
    (Net TL / net lot anlamsızdır: alışlar ucuzken, satışlar pahalıyken yapıldıysa net lot ile net TL ters işaretli olabilir.)
    """
    col = 'SUM(net_size)' if by == 'lot' else 'SUM(net_volume)'
    base = ('SELECT code, SUM(buy_volume), SUM(sell_volume), SUM(net_volume), '
            'SUM(net_size), COUNT(*), SUM(buy_size), SUM(sell_size) FROM bofa_daily '
            'WHERE trade_date BETWEEN ? AND ?' + _scope(include_funds) +
            ' GROUP BY code HAVING ' + col + ' {op} 0 ORDER BY ' + col + ' {ord} LIMIT ?')
    con = connect()
    try:
        def fetch(op, order):
            rows = con.execute(base.format(op=op, ord=order), (start, end, limit)).fetchall()
            return [{'code': c, 'buy': b or 0, 'sell': s or 0, 'net': n or 0,
                     'net_lot': lot or 0, 'days': d,
                     'buy_avg': (b / bs) if b and bs else None,
                     'sell_avg': (s / ss) if s and ss else None}
                    for c, b, s, n, lot, d, bs, ss in rows]
        return {'bought': fetch('>', 'DESC'), 'sold': fetch('<', 'ASC'), 'by': by}
    finally:
        con.close()


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'import':
        for r in import_file(sys.argv[2]):
            print(r)
        print(status())
    elif len(sys.argv) >= 2 and sys.argv[1] == 'status':
        print(status())
    else:
        print(__doc__)
