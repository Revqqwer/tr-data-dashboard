"""
TR 2Y ve 10Y tahvil faizi tarihsel verisi.
TradingView WebSocket protokolünü doğrudan kullanır — ek paket gerekmez.

Kullanım (PythonAnywhere bash):
    cd ~/tr-data-dashboard && python3.10 collect_tr_yields.py
"""
import json
import os
import random
import re
import sqlite3
import string
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'cache.db')
N_BARS  = 120   # ~10 yıl aylık veri

SYMBOLS = [
    ('TR02Y', 'TVC', 'tr2y'),
    ('TR10Y', 'TVC', 'tr10y'),
]


# ── TradingView WebSocket yardımcıları ────────────────────────────────────────

def _rand(n=12):
    return ''.join(random.choices(string.ascii_lowercase, k=n))


def _pack(func, args):
    body = json.dumps({'m': func, 'p': args}, separators=(',', ':'))
    return f'~m~{len(body)}~m~{body}'


def _parse(raw):
    """~m~N~m~... formatındaki mesajları parçala."""
    out = []
    while raw:
        m = re.match(r'^~m~(\d+)~m~', raw)
        if not m:
            break
        n     = int(m.group(1))
        start = m.end()
        out.append(raw[start: start + n])
        raw = raw[start + n:]
    return out


def fetch_tv_monthly(symbol, exchange, n_bars=N_BARS):
    """
    TradingView'dan aylık (1M) kapanış verisi çeker.
    Döner: list of (ym, close)  örn. [('2024-01', 36.5), ...]
    """
    from websocket import create_connection, WebSocketTimeoutException

    url = (
        'wss://data.tradingview.com/socket.io/websocket'
        '?from=chart%2F&date=&type=chart'
    )
    ws = create_connection(
        url,
        headers={'Origin': 'https://www.tradingview.com'},
        timeout=15,
    )

    chart_sess = 'cs_' + _rand()
    sym_json   = json.dumps({'adjustment': 'splits', 'symbol': f'{exchange}:{symbol}'})

    ws.send(_pack('set_auth_token',    ['unauthorized_user_token']))
    ws.send(_pack('chart_create_session', [chart_sess, '']))
    ws.send(_pack('resolve_symbol',    [chart_sess, 'ser_1', f'={sym_json}']))
    ws.send(_pack('create_series',     [chart_sess, 's1', 's1', 'ser_1', '1M', n_bars]))

    result = []
    attempts = 0

    while attempts < 30:
        try:
            raw = ws.recv()
        except WebSocketTimeoutException:
            attempts += 1
            continue
        except Exception:
            break

        for pkt in _parse(raw):
            # heartbeat
            if re.match(r'^~h~\d+$', pkt):
                ws.send(f'~m~{len(pkt)}~m~{pkt}')
                continue
            try:
                msg = json.loads(pkt)
            except Exception:
                continue

            if msg.get('m') == 'timescale_update':
                bars = (
                    msg.get('p', [{}] * 2)[1]
                       .get('s1', {})
                       .get('s', [])
                )
                for bar in bars:
                    v = bar.get('v', [])
                    if len(v) >= 5:
                        ts    = v[0]
                        close = v[4]
                        ym    = datetime.utcfromtimestamp(ts).strftime('%Y-%m')
                        result.append((ym, round(float(close), 2)))
                if result:
                    ws.close()
                    return result

        attempts += 1

    ws.close()
    return result


# ── DB ────────────────────────────────────────────────────────────────────────

def ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS tr_yield_cache (
        ym    TEXT PRIMARY KEY,
        tr2y  REAL,
        tr10y REAL
    )''')
    conn.commit()


def upsert(conn, ym, col, value):
    existing = conn.execute(
        'SELECT tr2y, tr10y FROM tr_yield_cache WHERE ym=?', (ym,)
    ).fetchone()
    if existing:
        new_2y  = value if col == 'tr2y'  else existing[0]
        new_10y = value if col == 'tr10y' else existing[1]
        conn.execute(
            'UPDATE tr_yield_cache SET tr2y=?, tr10y=? WHERE ym=?',
            (new_2y, new_10y, ym)
        )
    else:
        tr2y  = value if col == 'tr2y'  else None
        tr10y = value if col == 'tr10y' else None
        conn.execute(
            'INSERT INTO tr_yield_cache(ym, tr2y, tr10y) VALUES(?,?,?)',
            (ym, tr2y, tr10y)
        )


# ── Ana akış ─────────────────────────────────────────────────────────────────

def collect():
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("websocket-client kurulu değil:")
        print("  pip3.10 install --user websocket-client")
        return

    with sqlite3.connect(DB_PATH) as conn:
        ensure_table(conn)

        for symbol, exchange, col in SYMBOLS:
            print(f'Çekiliyor: {exchange}:{symbol} …', flush=True)
            try:
                rows = fetch_tv_monthly(symbol, exchange)
            except Exception as e:
                print(f'  → Hata: {e}')
                continue

            if not rows:
                print('  → Veri gelmedi')
                continue

            for ym, close in rows:
                upsert(conn, ym, col, close)
            conn.commit()
            print(f'  → {len(rows)} ay kaydedildi')

    # Özet
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            'SELECT ym, tr2y, tr10y FROM tr_yield_cache ORDER BY ym DESC LIMIT 8'
        ).fetchall()
    print(f'\nSon 8 ay ({DB_PATH}):')
    print(f"{'Ay':<10} {'TR2Y':>8} {'TR10Y':>8}")
    for ym, tr2y, tr10y in rows:
        print(f"{ym:<10} {(str(tr2y) if tr2y else '-'):>8} {(str(tr10y) if tr10y else '-'):>8}")


if __name__ == '__main__':
    collect()
