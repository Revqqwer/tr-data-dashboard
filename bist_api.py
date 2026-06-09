# -*- coding: utf-8 -*-
"""
BİST Tracker — Flask Blueprint
Route: /bist/
API:   /bist/api/...

TradingView'dan WebSocket ile veri çeker (tvDatafeed gerekmez).
"""
import json, re, sqlite3, threading, time, logging, random, string
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, session, redirect, url_for, send_from_directory

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

log = logging.getLogger(__name__)

BASE_DIR       = Path(__file__).parent
BIST_DB_PATH   = BASE_DIR / 'data' / 'bist_cache.db'
EXCEL_PATH     = BASE_DIR / 'data' / 'Endeksler.xlsx'
BIST_STATIC    = BASE_DIR / 'bist_static'
EXCHANGE       = 'BIST'
REFRESH_HRS    = 4
STOCK_TTL_HRS  = 24

bist_bp = Blueprint('bist', __name__, url_prefix='/bist')

# ── Endeks listesi ─────────────────────────────────────────────────────────────
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
    "BIST MENKUL KIYM Y.O.":     "XYORT",
    "BIST TURIZM":               "XTRZM",
}

# period → (n_bars, TV_resolution)
PERIOD_TV = {
    '1h': (5,    '1D'),   # 1 Hafta — 5 iş günü
    '1a': (22,   '1D'),   # 1 Ay    — ~22 iş günü
    '3a': (65,   '1D'),   # 3 Ay    — ~65 iş günü
    '6a': (130,  '1D'),   # 6 Ay    — ~130 iş günü
    '1y': (252,  '1D'),   # 1 Yıl   — ~252 iş günü
    '3y': (156,  '1W'),   # 3 Yıl   — 3 × 52 hafta
    '5y': (260,  '1W'),   # 5 Yıl   — 5 × 52 hafta
}


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
    """
    TradingView WebSocket üzerinden tarihsel bar verisi çeker.
    resolution: '1D' (günlük) | '1W' (haftalık) | '1M' (aylık)
    Döner: (dates: list[str], closes: list[float]) ya da (None, None)
    """
    try:
        from websocket import create_connection
    except ImportError:
        log.error('websocket-client kurulu değil: pip3.10 install --user websocket-client')
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
                            dt    = datetime.utcfromtimestamp(ts)
                            dates.append(dt.strftime('%Y-%m-%d'))
                            closes.append(round(float(close), 2))
                    if dates:
                        ws.close()
                        return dates, closes

                elif msg.get('m') == 'symbol_error':
                    log.warning(f'TV symbol_error: {exchange}:{symbol}')
                    ws.close()
                    return None, None

            attempts += 1

        ws.close()
        return (dates, closes) if dates else (None, None)

    except Exception as e:
        log.warning(f'TV WS hata {exchange}:{symbol}: {e}')
        return None, None


# ── Excel: endeks bileşenleri ─────────────────────────────────────────────────
index_compositions = {}   # { "BIST 100": [{kod, ad, ticker}, ...] }


def load_excel():
    if not _HAS_OPENPYXL:
        log.warning('openpyxl kurulu değil, hisse verisi devre dışı')
        return
    if not EXCEL_PATH.exists():
        log.warning(f'Endeksler.xlsx bulunamadı: {EXCEL_PATH}')
        return
    try:
        wb = openpyxl.load_workbook(str(EXCEL_PATH))
        ws = wb.active
        current, stocks = None, []
        skip = {'Endeksler Listesi', 'Sira', None, 'Kod'}
        for row in ws.iter_rows(values_only=True):
            v0, v1, v2 = row[0], row[1], row[2]
            if (isinstance(v0, str) and v1 is None and v2 is None
                    and v0.strip() not in skip and v0.strip()):
                if current:
                    index_compositions[current] = stocks
                current, stocks = v0.strip(), []
            elif current and v1 and v1 not in ('Kod', ''):
                stocks.append({'kod': str(v1), 'ad': str(v2 or v1),
                               'ticker': f'{v1}.IS'})
        if current:
            index_compositions[current] = stocks
        log.info(f'BİST Excel: {len(index_compositions)} endeks yüklendi')
    except Exception as e:
        log.error(f'BİST Excel hata: {e}')


def normalize(s):
    s = s.upper()
    for fr, to in [('Ü','U'),('Ö','O'),('Ç','C'),('Ş','S'),('İ','I'),('Ğ','G'),
                   ('ü','u'),('ö','o'),('ç','c'),('ş','s'),('ı','i'),('ğ','g')]:
        s = s.replace(fr, to)
    return re.sub(r'[^A-Z0-9]+', ' ', s).strip()


def find_excel_key(name):
    n = normalize(name)
    for k in index_compositions:
        if normalize(k) == n:
            return k
    return None


# ── SQLite DB ─────────────────────────────────────────────────────────────────

def get_bist_conn():
    conn = sqlite3.connect(str(BIST_DB_PATH), timeout=30,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_bist_db():
    with get_bist_conn() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_returns (
                index_name TEXT,
                period     TEXT,
                data       TEXT,
                updated_at INTEGER,
                PRIMARY KEY (index_name, period)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_price_history (
                kod        TEXT PRIMARY KEY,
                dates      TEXT,
                prices     TEXT,
                updated_at INTEGER
            )""")
        conn.commit()
    log.info('BİST DB hazır')


def db_get_history(period):
    with get_bist_conn() as conn:
        rows = conn.execute(
            'SELECT name, dates, values_, last_price, pct, updated_at '
            'FROM index_history WHERE period=?', (period,)).fetchall()
    if not rows:
        return None, 0
    result, oldest = {}, int(time.time())
    for r in rows:
        result[r['name']] = {
            'dates':     json.loads(r['dates']),
            'values':    json.loads(r['values_']),
            'lastPrice': r['last_price'],
            'pct':       r['pct'],
        }
        if r['updated_at'] < oldest:
            oldest = r['updated_at']
    return result, oldest


def db_save_history(period, data):
    now = int(time.time())
    with get_bist_conn() as conn:
        for name, d in data.items():
            conn.execute("""
                INSERT OR REPLACE INTO index_history
                (name, period, dates, values_, last_price, pct, updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (name, period,
                 json.dumps(d['dates']), json.dumps(d['values']),
                 d['lastPrice'], d['pct'], now))
        conn.commit()
    log.info(f'BİST DB: {len(data)} endeks kaydedildi (period={period})')


def db_get_stocks(index_name, period):
    with get_bist_conn() as conn:
        row = conn.execute(
            'SELECT data, updated_at FROM stock_returns WHERE index_name=? AND period=?',
            (index_name, period)).fetchone()
    if not row:
        return None, 0
    return json.loads(row['data']), row['updated_at']


def db_save_stocks(index_name, period, data):
    with get_bist_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO stock_returns (index_name, period, data, updated_at)
            VALUES (?,?,?,?)""",
            (index_name, period, json.dumps(data), int(time.time())))
        conn.commit()


def is_fresh(updated_at, ttl_hours):
    return (time.time() - updated_at) < ttl_hours * 3600


def fetch_stocks_from_tv(index_name, period):
    """Belirli endeks bileşenlerinin getirisini TV'den çeker."""
    key = find_excel_key(index_name)
    if not key:
        return None
    stocks = index_compositions[key]
    n_bars, resolution = PERIOD_TV.get(period, (365, '1D'))

    returns, prices = {}, {}
    for s in stocks:
        kod = s['kod']
        dates, closes = fetch_tv_ws(kod, EXCHANGE, n_bars, resolution)
        if dates and len(closes) >= 2:
            returns[kod] = round((closes[-1] / closes[0] - 1) * 100, 2)
            prices[kod]  = closes[-1]
        time.sleep(0.3)

    result = [{
        'kod':   s['kod'], 'ad': s['ad'], 'ticker': s['ticker'],
        'pct':   returns.get(s['kod']),
        'price': prices.get(s['kod']),
    } for s in stocks]
    result.sort(key=lambda x: x['pct'] if x['pct'] is not None else -9999, reverse=True)
    return result




# ── Blueprint rotaları ─────────────────────────────────────────────────────────

def _auth():
    return True  # Tüm okuma endpoint'leri artık public


@bist_bp.route('/')
@bist_bp.route('')
def bist_index():
    return send_from_directory(str(BIST_STATIC), 'index.html')


@bist_bp.route('/karsilastirma')
def bist_karsilastirma():
    return send_from_directory(str(BIST_STATIC), 'karsilastirma.html')


@bist_bp.route('/api/indices')
def api_indices():
    return jsonify([{'name': k, 'symbol': v} for k, v in ENDEKSLER.items()])


@bist_bp.route('/api/history')
def api_history():
    period = request.args.get('period', '1y').lower()

    # 1h: 1y verisinden son 7 işlem gününü alıp yeniden normalize et (base=100)
    if period == '1h':
        data, ts = db_get_history('1y')
        if data:
            sliced = {}
            for name, d in data.items():
                dates  = d.get('dates', [])
                values = d.get('values', [])  # base=100 normalize
                n = min(7, len(dates))
                if n < 2:
                    sliced[name] = d
                    continue
                w_vals = values[-n:]
                base = w_vals[0]
                renorm = [round(v / base * 100, 2) for v in w_vals] if base else w_vals
                sliced[name] = {
                    'dates':     dates[-n:],
                    'values':    renorm,
                    'lastPrice': d.get('lastPrice'),
                    'pct':       round((renorm[-1] / 100 - 1) * 100, 2),
                }
            return jsonify(sliced)
        return jsonify({'_loading': True})

    data, _ = db_get_history(period)
    if data:
        return jsonify(data)
    # DB boş → collect_bist.py henüz çalışmamış
    return jsonify({'_loading': True})


@bist_bp.route('/api/stocks')
def api_stocks():
    index_name = request.args.get('index', '')
    period     = request.args.get('period', '1y').lower()
    start      = request.args.get('start', '').strip()
    end        = request.args.get('end',   '').strip()

    # ── Özel tarih aralığı modu ───────────────────────────────────────────────
    if start and end and start < end:
        key = find_excel_key(index_name)
        if not key:
            return jsonify({'error': f'Endeks bulunamadı: {index_name}'}), 404
        stocks = index_compositions.get(key, [])
        result = []
        with get_bist_conn() as conn:
            for s in stocks:
                kod = s['kod']
                row = conn.execute(
                    'SELECT dates, prices FROM stock_price_history WHERE kod=?',
                    (kod,)).fetchone()
                if not row:
                    result.append({'kod': kod, 'ad': s.get('ad', kod),
                                   'ticker': s.get('ticker', ''), 'pct': None, 'price': None})
                    continue
                dates  = json.loads(row['dates'])
                prices = json.loads(row['prices'])
                pairs  = [(d, p) for d, p in zip(dates, prices) if start <= d <= end]
                if len(pairs) < 2:
                    result.append({'kod': kod, 'ad': s.get('ad', kod),
                                   'ticker': s.get('ticker', ''), 'pct': None, 'price': None})
                    continue
                _, fprices = zip(*pairs)
                base = fprices[0]
                pct  = round((fprices[-1] / base - 1) * 100, 2) if base else None
                result.append({
                    'kod':    kod,
                    'ad':     s.get('ad', kod),
                    'ticker': s.get('ticker', ''),
                    'price':  round(fprices[-1], 2),
                    'pct':    pct,
                })
        result.sort(key=lambda x: x['pct'] if x['pct'] is not None else -9999, reverse=True)
        return jsonify(result)

    # ── Normal dönem modu ─────────────────────────────────────────────────────
    data, updated = db_get_stocks(index_name, period)
    if data:
        if not is_fresh(updated, STOCK_TTL_HRS):
            threading.Thread(
                target=lambda: db_save_stocks(
                    index_name, period,
                    fetch_stocks_from_tv(index_name, period) or data),
                daemon=True).start()
        return jsonify(data)
    if not find_excel_key(index_name):
        avail = list(index_compositions.keys())
        return jsonify({
            'error': f'Bu endeks için bileşen listesi bulunamadı: {index_name}. '
                     f'Endeksler.xlsx dosyasında bu endeks tanımlı olmayabilir.',
            'available': avail,
        }), 404
    log.info(f'BİST hisse çekiliyor: {index_name} {period}')
    result = fetch_stocks_from_tv(index_name, period)
    if result:
        db_save_stocks(index_name, period, result)
        return jsonify(result)
    return jsonify({'error': 'Veri çekilemedi'}), 500


@bist_bp.route('/api/history/custom')
def api_history_custom():
    """Özel tarih aralığı için endeks getirilerini döner."""
    start = request.args.get('start', '').strip()
    end   = request.args.get('end',   '').strip()
    if not start or not end or start >= end:
        return jsonify({'error': 'Geçerli start ve end tarihi gerekli (YYYY-MM-DD)'}), 400

    # Önce günlük veri (1y) dene — daha sonra haftalık (5y) fallback
    data = None
    for period_key in ('1y', '5y', '3y'):
        d, _ = db_get_history(period_key)
        if not d:
            continue
        sample = next(iter(d.values()), {})
        dates  = sample.get('dates', [])
        if dates and dates[0] <= start:
            data = d
            break
    if data is None:
        # en uzun elimizde ne varsa kullan
        for period_key in ('5y', '3y', '1y', '6a'):
            d, _ = db_get_history(period_key)
            if d:
                data = d
                break

    if not data:
        return jsonify({'_loading': True})

    result = {}
    for name, d in data.items():
        dates  = d.get('dates',  [])
        values = d.get('values', [])
        # Tarih aralığını filtrele
        pairs = [(dt, v) for dt, v in zip(dates, values) if start <= dt <= end]
        if len(pairs) < 2:
            continue
        fdates, fvalues = zip(*pairs)
        base = fvalues[0]
        if not base:
            continue
        reindexed = [round(v / base * 100, 2) for v in fvalues]
        result[name] = {
            'dates':     list(fdates),
            'values':    reindexed,
            'lastPrice': d.get('lastPrice'),
            'pct':       round(reindexed[-1] - 100, 2),
        }

    if not result:
        return jsonify({'_empty': True, 'message': 'Seçilen tarih aralığında veri bulunamadı'})

    return jsonify(result)


@bist_bp.route('/api/returns-summary')
def api_returns_summary():
    periods = list(PERIOD_TV.keys())
    with get_bist_conn() as conn:
        rows = conn.execute('SELECT name, period, pct FROM index_history').fetchall()
    summary = {}
    for r in rows:
        if r['period'] not in periods:
            continue
        if r['name'] not in summary:
            summary[r['name']] = {}
        summary[r['name']][r['period']] = r['pct']
    return jsonify({'periods': periods, 'data': summary})


@bist_bp.route('/api/refresh')
def api_refresh():
    # Veri yenileme komutu — sadece üyeler
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    import subprocess, sys
    script = str(BASE_DIR / 'collect_bist.py')
    threading.Thread(
        target=lambda: subprocess.run([sys.executable, script]),
        daemon=True
    ).start()
    return jsonify({'status': 'collect_bist.py arka planda başlatıldı'})


@bist_bp.route('/api/cache-status')
def api_cache_status():
    with get_bist_conn() as conn:
        rows = conn.execute(
            'SELECT period, COUNT(*) as cnt, MAX(updated_at) as ts '
            'FROM index_history GROUP BY period').fetchall()
    now = time.time()
    return jsonify([{
        'period':  r['period'],
        'endeks':  r['cnt'],
        'age_min': round((now - r['ts']) / 60),
        'fresh':   is_fresh(r['ts'], REFRESH_HRS),
    } for r in rows])


# ── Startup ───────────────────────────────────────────────────────────────────
_started = False


def bist_startup():
    global _started
    if _started:
        return
    _started = True
    init_bist_db()
    load_excel()
    log.info('BİST Tracker başlatıldı (veri: collect_bist.py / PA scheduled task)')


bist_startup()
