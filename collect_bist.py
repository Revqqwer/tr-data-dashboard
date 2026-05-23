"""
BİST endeks + hisse verisi kolektörü — PythonAnywhere scheduled task.

Optimizasyon:
  • Her endeks için 2 TV isteği (günlük + haftalık), slice ile 6 dönem.
  • Her benzersiz hisse için 2 TV isteği; tüm endeks×dönem kombinasyonları hesaplanır.
  • Tüm veri geçici DB'ye yazılır, bitince atomik os.replace() ile canlıya alınır.

Kullanım:
    cd ~/tr-data-dashboard && python3.10 collect_bist.py

PA scheduled task önerisi: her gün 20:00 UTC
"""
import json, os, re, sqlite3, time, logging, random, string
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent
DB_PATH    = BASE_DIR / 'data' / 'bist_cache.db'
TEMP_PATH  = BASE_DIR / 'data' / 'bist_cache_building.db'
EXCEL_PATH = BASE_DIR / 'data' / 'Endeksler.xlsx'
EXCHANGE   = 'BIST'

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
    "FAİZ (TLREF)":              "BISTTLREF",
}

# 1 TV isteğiyle tüm günlük dönemleri karşıla: 365 günlük bar çek, slice et
DAILY_N      = 365
DAILY_SLICES = {'1a': 31, '3a': 93, '6a': 186, '1y': 365}

# 1 TV isteğiyle tüm haftalık dönemleri karşıla: 260 haftalık bar çek, slice et
# 1 yıl ≈ 52 haftalık bar → 3y=156, 5y=260
WEEKLY_N      = 260
WEEKLY_SLICES = {'3y': 156, '5y': 260}


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


# ── Excel: endeks bileşenleri ─────────────────────────────────────────────────

def _normalize(s):
    s = s.upper()
    for fr, to in [('Ü','U'),('Ö','O'),('Ç','C'),('Ş','S'),('İ','I'),('Ğ','G'),
                   ('ü','u'),('ö','o'),('ç','c'),('ş','s'),('ı','i'),('ğ','g')]:
        s = s.replace(fr, to)
    return re.sub(r'[^A-Z0-9]+', ' ', s).strip()


def load_excel():
    """Endeksler.xlsx'ten endeks→hisse listesini yükler."""
    if not _HAS_OPENPYXL:
        log.warning('openpyxl yok, hisse koleksiyonu atlanıyor')
        return {}
    if not EXCEL_PATH.exists():
        log.warning(f'Endeksler.xlsx bulunamadı: {EXCEL_PATH}')
        return {}
    try:
        wb = openpyxl.load_workbook(str(EXCEL_PATH))
        ws = wb.active
        compositions = {}
        current, stocks = None, []
        skip = {'Endeksler Listesi', 'Sira', None, 'Kod'}
        for row in ws.iter_rows(values_only=True):
            v0, v1, v2 = row[0], row[1], row[2]
            if (isinstance(v0, str) and v1 is None and v2 is None
                    and v0.strip() not in skip and v0.strip()):
                if current:
                    compositions[current] = stocks
                current, stocks = v0.strip(), []
            elif current and v1 and v1 not in ('Kod', ''):
                stocks.append({'kod': str(v1), 'ad': str(v2 or v1),
                               'ticker': f'{v1}.IS'})
        if current:
            compositions[current] = stocks
        log.info(f'Excel: {len(compositions)} endeks yüklendi')
        return compositions
    except Exception as e:
        log.error(f'Excel hata: {e}')
        return {}


def collect_stocks(conn, compositions):
    """Tüm endeks bileşenlerinin getirisini TV'den çekip DB'ye yazar."""
    if not compositions:
        log.warning('Excel verisi yok, hisse koleksiyonu atlanıyor')
        return

    # ENDEKSLER anahtarları ile Excel anahtarlarını eşleştir
    endeks_stocks = {}   # dashboard_name → [stock_dicts]
    for dash_name in ENDEKSLER:
        n = _normalize(dash_name)
        for excel_key, stocks in compositions.items():
            if _normalize(excel_key) == n:
                endeks_stocks[dash_name] = stocks
                break

    # Benzersiz hisseler
    unique = {}   # kod → stock_info
    for stocks in endeks_stocks.values():
        for s in stocks:
            if s['kod'] not in unique:
                unique[s['kod']] = s

    total_s = len(unique)
    log.info(f'Hisse kolektörü: {total_s} benzersiz hisse, {len(endeks_stocks)} endeks')

    # Her benzersiz hisse için 2 TV isteği → 6 dönem slice
    returns_map  = {}   # kod → {period: pct}
    price_map    = {}   # kod → last_price
    history_map  = {}   # kod → {dates: [...], prices: [...]}  ← YENİ

    for i, (kod, s) in enumerate(unique.items(), 1):
        if i == 1 or i % 100 == 0:
            log.info(f'  Hisse [{i}/{total_s}]')

        d_dates, d_closes = fetch_tv_ws(kod, EXCHANGE, DAILY_N, '1D')
        time.sleep(0.3)
        w_dates, w_closes = fetch_tv_ws(kod, EXCHANGE, WEEKLY_N, '1W')
        time.sleep(0.3)

        rets = {}
        if d_dates and len(d_closes) >= 2:
            price_map[kod] = d_closes[-1]
            # Tam günlük geçmişi sakla (custom tarih sorguları için)
            history_map[kod] = {
                'dates':  d_dates,
                'prices': [round(c, 2) for c in d_closes],
            }
            for period, n_bars in DAILY_SLICES.items():
                n2 = min(n_bars, len(d_closes))
                c  = d_closes[-n2:]
                if c[0]:
                    rets[period] = round((c[-1] / c[0] - 1) * 100, 2)
        if w_dates and len(w_closes) >= 2:
            for period, n_bars in WEEKLY_SLICES.items():
                n2 = min(n_bars, len(w_closes))
                c  = w_closes[-n2:]
                if c[0]:
                    rets[period] = round((c[-1] / c[0] - 1) * 100, 2)
        if rets:
            returns_map[kod] = rets

    log.info(f'Hisse çekimi bitti ({len(returns_map)}/{total_s} başarılı), DB\'ye yazılıyor...')

    now = int(time.time())
    all_periods = list(DAILY_SLICES.keys()) + list(WEEKLY_SLICES.keys())
    for dash_name, stocks in endeks_stocks.items():
        for period in all_periods:
            result = []
            for s in stocks:
                kod = s['kod']
                result.append({
                    'kod':   kod,
                    'ad':    s['ad'],
                    'ticker': s['ticker'],
                    'pct':   returns_map.get(kod, {}).get(period),
                    'price': price_map.get(kod),
                })
            result.sort(
                key=lambda x: x['pct'] if x['pct'] is not None else -9999,
                reverse=True)
            conn.execute("""
                INSERT OR REPLACE INTO stock_returns
                (index_name, period, data, updated_at)
                VALUES (?,?,?,?)""",
                (dash_name, period, json.dumps(result), now))
        conn.commit()

    log.info(f'Hisse DB yazıldı: {len(endeks_stocks)} endeks × {len(all_periods)} dönem')

    # Tam günlük fiyat geçmişini kaydet (custom tarih sorguları için)
    for kod, hist in history_map.items():
        conn.execute("""
            INSERT OR REPLACE INTO stock_price_history (kod, dates, prices, updated_at)
            VALUES (?,?,?,?)""",
            (kod, json.dumps(hist['dates']), json.dumps(hist['prices']), now))
    conn.commit()
    log.info(f'Hisse fiyat geçmişi kaydedildi: {len(history_map)} hisse')


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

def save(conn, name, period, data):
    conn.execute("""
        INSERT OR REPLACE INTO index_history
        (name, period, dates, values_, last_price, pct, updated_at)
        VALUES (?,?,?,?,?,?,?)""",
        (name, period,
         json.dumps(data['dates']), json.dumps(data['values']),
         data['lastPrice'], data['pct'], int(time.time())))


# ── Ana akış ─────────────────────────────────────────────────────────────────

def db_save_endeks(rows, conn):
    """Verilen bağlantıya endeks verilerini yaz."""
    for (name, period, data) in rows:
        save(conn, name, period, data)
    conn.commit()


def collect():
    try:
        import websocket  # noqa
    except ImportError:
        log.error('websocket-client kurulu değil: pip3.10 install --user websocket-client')
        return

    log.info(f'BİST kolektör başladı — {len(ENDEKSLER)} endeks')
    log.info(f'Geçici DB: {TEMP_PATH}')
    t0 = time.time()

    # Temiz geçici DB aç — Flask'ın bist_cache.db'sine hiç dokunmuyoruz
    if TEMP_PATH.exists():
        TEMP_PATH.unlink()

    conn = sqlite3.connect(str(TEMP_PATH))
    try:
        init_db(conn)

        total = len(ENDEKSLER)
        for i, (name, symbol) in enumerate(ENDEKSLER.items(), 1):
            log.info(f'[{i:2d}/{total}] {name} ({symbol})')
            rows = []

            # ── Günlük: 1A / 3A / 6A / 1Y ────────────────────────────
            d_dates, d_closes = fetch_tv_ws(symbol, EXCHANGE, DAILY_N, '1D')
            if d_dates:
                for period, n in DAILY_SLICES.items():
                    data = make_period_data(d_dates, d_closes, n)
                    if data:
                        rows.append((name, period, data))
                log.info(f'         günlük OK  ({len(d_dates)} bar, '
                         f'1Y={make_period_data(d_dates,d_closes,365)["pct"]:+.1f}%)')
            else:
                log.warning(f'         günlük veri alınamadı')

            time.sleep(0.5)

            # ── Haftalık: 3Y / 5Y ─────────────────────────────────────
            w_dates, w_closes = fetch_tv_ws(symbol, EXCHANGE, WEEKLY_N, '1W')
            if w_dates:
                for period, n in WEEKLY_SLICES.items():
                    data = make_period_data(w_dates, w_closes, n)
                    if data:
                        rows.append((name, period, data))
                log.info(f'         haftalık OK ({len(w_dates)} bar)')
            else:
                log.warning(f'         haftalık veri alınamadı')

            if rows:
                db_save_endeks(rows, conn)

            time.sleep(0.5)

        # ── Hisse getirileri ──────────────────────────────────────
        compositions = load_excel()
        collect_stocks(conn, compositions)

    finally:
        conn.close()

    # Tüm veriler geçici DB'ye yazıldı — atomik swap
    os.replace(str(TEMP_PATH), str(DB_PATH))
    log.info(f'DB güncellendi: {DB_PATH}')

    elapsed = round(time.time() - t0)
    log.info(f'Tamamlandı — {elapsed // 60}d {elapsed % 60}s')


if __name__ == '__main__':
    collect()
