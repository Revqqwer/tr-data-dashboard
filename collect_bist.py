"""
BİST endeks verisi kolektörü — PythonAnywhere scheduled task.

Optimizasyon: her endeks için sadece 2 TV isteği (günlük + haftalık)
ve slice ile tüm 6 dönem hesaplanır. ~5-10 dakika sürer.

Kullanım (PythonAnywhere bash / scheduled task):
    cd ~/tr-data-dashboard && python3.10 collect_bist.py

PA scheduled task önerisi: her gün 20:00 UTC
"""
import json, re, sqlite3, time, logging, random, string
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent
DB_PATH   = BASE_DIR / 'data' / 'bist_cache.db'
EXCHANGE  = 'BIST'

ENDEKSLER = {
    "BIST 100":                  "XU100",
    "BIST 30":                   "XU030",
    "BIST TUM":                  "XUTUM",
    "BIST HALKA ARZ":            "XHARZ",
    "BIST GIDA ICECEK":          "XGIDA",
    "BIST KIMYA PETROL PLASTIK": "XKMYA",
    "BIST MADENCILIK":           "XMADN",
    "BIST METAL ANA":            "XMANA",
    "BIST METAL ESYA MAKINA":    "XMESY",
    "BIST ORMAN KAGIT BASIM":    "XKAGT",
    "BIST TAS TOPRAK":           "XTAST",
    "BIST TEKSTIL DERI":         "XTEKS",
    "BIST ELEKTRIK":             "XELKT",
    "BIST ILETISIM":             "XILTM",
    "BIST INSAAT":               "XINSA",
    "BIST SPOR":                 "XSPOR",
    "BIST TICARET":              "XTCRT",
    "BIST ULASTIRMA":            "XULAS",
    "BIST BANKA":                "XBANK",
    "BIST SIGORTA":              "XSGRT",
    "BIST FIN KIR FAKTORING":    "XFINK",
    "BIST HOLDING VE YATIRIM":   "XHOLD",
    "BIST GAYRIMENKUL YAT ORT":  "XGMYO",
    "BIST TEKNOLOJI":            "XTKJS",
    "BIST BILISIM":              "XBLSM",
    "BIST 100-30":               "XYUZO",
    "BIST TUM-100":              "XTUMY",
    "BIST SINAI":                "XUSIN",
    "BIST HIZMETLER":            "XUHIZ",
    "BIST MALI":                 "XUMAL",
    "BIST ARACI KURUM":          "XAKUR",
    "BIST MENKUL KIYM YO":       "XYORT",
    "BIST TURIZM":               "XTRZM",
}

# 1 TV isteğiyle tüm günlük dönemleri karşıla: 365 günlük bar çek, slice et
DAILY_N      = 365
DAILY_SLICES = {'1a': 31, '3a': 93, '6a': 186, '1y': 365}

# 1 TV isteğiyle tüm haftalık dönemleri karşıla: 1825 haftalık bar çek, slice et
WEEKLY_N      = 1825
WEEKLY_SLICES = {'3y': 1100, '5y': 1825}


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
        n     = int(m.group(1))
        start = m.end()
        out.append(raw[start: start + n])
        raw = raw[start + n:]
    return out

def fetch_tv_ws(symbol, exchange, n_bars, resolution='1D'):
    """TradingView WebSocket'ten tarihsel bar verisi çeker."""
    try:
        from websocket import create_connection
    except ImportError:
        log.error('websocket-client yok: pip3.10 install --user websocket-client')
        return None, None

    try:
        ws = create_connection(
            'wss://data.tradingview.com/socket.io/websocket?from=chart%2F&date=&type=chart',
            headers={'Origin': 'https://www.tradingview.com'},
            timeout=20,
        )
        chart_sess = 'cs_' + _rand()
        sym_json   = json.dumps({'adjustment': 'splits', 'symbol': f'{exchange}:{symbol}'})

        ws.send(_pack('set_auth_token',       ['unauthorized_user_token']))
        ws.send(_pack('chart_create_session', [chart_sess, '']))
        ws.send(_pack('resolve_symbol',       [chart_sess, 'ser_1', f'={sym_json}']))
        ws.send(_pack('create_series',        [chart_sess, 's1', 's1', 'ser_1', resolution, n_bars]))

        dates, closes = [], []
        attempts = 0

        while attempts < 40:
            try:
                raw = ws.recv()
            except Exception:
                attempts += 1
                continue

            for pkt in _parse(raw):
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
                            dt = datetime.utcfromtimestamp(v[0])
                            dates.append(dt.strftime('%Y-%m-%d'))
                            closes.append(round(float(v[4]), 2))
                    if dates:
                        ws.close()
                        return dates, closes

                elif msg.get('m') == 'symbol_error':
                    ws.close()
                    return None, None

            attempts += 1

        ws.close()
        return (dates, closes) if dates else (None, None)

    except Exception as e:
        log.warning(f'TV WS hata {exchange}:{symbol}: {e}')
        return None, None


# ── Hesaplama ─────────────────────────────────────────────────────────────────

def make_period_data(dates, closes, n):
    """Son n barı alıp normalize eder (base=100)."""
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


# ── DB ────────────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_history (
            name       TEXT,
            period     TEXT,
            dates      TEXT,
            values_    TEXT,
            last_price REAL,
            pct        REAL,
            updated_at INTEGER,
            PRIMARY KEY (name, period)
        )""")
    conn.commit()

def save(conn, name, period, data):
    conn.execute("""
        INSERT OR REPLACE INTO index_history
        (name, period, dates, values_, last_price, pct, updated_at)
        VALUES (?,?,?,?,?,?,?)""",
        (name, period,
         json.dumps(data['dates']), json.dumps(data['values']),
         data['lastPrice'], data['pct'], int(time.time())))


# ── Ana akış ─────────────────────────────────────────────────────────────────

def collect():
    try:
        import websocket  # noqa
    except ImportError:
        log.error('websocket-client kurulu değil: pip3.10 install --user websocket-client')
        return

    log.info(f'BİST kolektör başladı — {len(ENDEKSLER)} endeks')
    t0 = time.time()

    with sqlite3.connect(str(DB_PATH)) as conn:
        init_db(conn)
        total = len(ENDEKSLER)

        for i, (name, symbol) in enumerate(ENDEKSLER.items(), 1):
            log.info(f'[{i:2d}/{total}] {name} ({symbol})')

            # ── Günlük: 1A / 3A / 6A / 1Y ────────────────────────
            d_dates, d_closes = fetch_tv_ws(symbol, EXCHANGE, DAILY_N, '1D')
            if d_dates:
                for period, n in DAILY_SLICES.items():
                    data = make_period_data(d_dates, d_closes, n)
                    if data:
                        save(conn, name, period, data)
                log.info(f'         günlük OK  ({len(d_dates)} bar, '
                         f'1Y={make_period_data(d_dates,d_closes,365)["pct"]:+.1f}%)')
            else:
                log.warning(f'         günlük veri alınamadı')

            time.sleep(0.5)

            # ── Haftalık: 3Y / 5Y ─────────────────────────────────
            w_dates, w_closes = fetch_tv_ws(symbol, EXCHANGE, WEEKLY_N, '1W')
            if w_dates:
                for period, n in WEEKLY_SLICES.items():
                    data = make_period_data(w_dates, w_closes, n)
                    if data:
                        save(conn, name, period, data)
                log.info(f'         haftalık OK ({len(w_dates)} bar)')
            else:
                log.warning(f'         haftalık veri alınamadı')

            conn.commit()
            time.sleep(0.5)

    elapsed = round(time.time() - t0)
    log.info(f'Tamamlandı — {elapsed // 60}d {elapsed % 60}s')


if __name__ == '__main__':
    collect()
