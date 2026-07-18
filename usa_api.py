# -*- coding: utf-8 -*-
"""
ABD Endeksleri Karşılaştırma — Flask Blueprint
Route: /usa/
API:   /usa/api/...

Veri collect_usa.py (PA scheduled task) ile data/usa_cache.db'ye yazılır;
bu blueprint sadece DB'den okuyup statik sayfayı sunar (web yolunda TV çağrısı yok).
"""
import json, sqlite3, time, logging, subprocess, sys
from pathlib import Path
from flask import Blueprint, jsonify, request, send_from_directory

log = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
USA_DB_PATH = BASE_DIR / 'data' / 'usa_cache.db'
USA_STATIC  = BASE_DIR / 'usa_static'

usa_bp = Blueprint('usa', __name__, url_prefix='/usa')

# ── ABD endeksleri: ad → "BORSA:SEMBOL" (TradingView) ──────────────────────────
# Ana endeksler + ölçek + SPDR sektör ETF'leri (ABD sektör karşılaştırması için standart)
USA_INDICES = {
    "S&P 500":                 "SP:SPX",
    "Nasdaq 100":              "NASDAQ:NDX",
    "Nasdaq Composite":        "NASDAQ:IXIC",
    "Dow Jones":               "DJ:DJI",
    "Russell 2000":            "TVC:RUT",
    "NYSE Composite":          "TVC:NYA",
    "Dow Ulaştırma":           "DJ:DJT",
    "Dow Kamu Hizmetleri":     "DJ:DJU",
    "Yarı İletken (SOX)":      "NASDAQ:SOX",
    "VIX (Oynaklık)":          "TVC:VIX",
    "S&P MidCap 400":          "AMEX:MDY",
    "S&P SmallCap 600":        "AMEX:IJR",
    "Sektör: Teknoloji":            "AMEX:XLK",
    "Sektör: Finans":               "AMEX:XLF",
    "Sektör: Sağlık":               "AMEX:XLV",
    "Sektör: Tüketici (Dayanıklı)": "AMEX:XLY",
    "Sektör: Tüketici (Temel)":     "AMEX:XLP",
    "Sektör: Sanayi":               "AMEX:XLI",
    "Sektör: Enerji":               "AMEX:XLE",
    "Sektör: Malzeme":              "AMEX:XLB",
    "Sektör: Kamu Hizmetleri":      "AMEX:XLU",
    "Sektör: Gayrimenkul":          "AMEX:XLRE",
    "Sektör: İletişim":             "AMEX:XLC",
}


# ── SQLite ─────────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(str(USA_DB_PATH), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_usa_db():
    USA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute("""
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
        c.commit()


def db_get_history(period):
    try:
        with _conn() as c:
            rows = c.execute(
                'SELECT name, dates, values_, last_price, pct, updated_at '
                'FROM index_history WHERE period=?', (period,)).fetchall()
    except Exception:
        return None, 0
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


# ── Rotalar ────────────────────────────────────────────────────────────────────

@usa_bp.route('/')
@usa_bp.route('')
def usa_index():
    return send_from_directory(str(USA_STATIC), 'index.html')


@usa_bp.route('/api/indices')
def api_indices():
    return jsonify([{'name': k, 'symbol': v} for k, v in USA_INDICES.items()])


@usa_bp.route('/api/history')
def api_history():
    period = request.args.get('period', '1y').lower()

    # 1h (1 hafta): 1y verisinden son 7 barı alıp yeniden normalize et
    if period == '1h':
        data, _ = db_get_history('1y')
        if data:
            out = {}
            for name, d in data.items():
                dates, values = d.get('dates', []), d.get('values', [])
                n = min(7, len(dates))
                if n < 2:
                    out[name] = d
                    continue
                w = values[-n:]
                base = w[0]
                renorm = [round(v / base * 100, 2) for v in w] if base else w
                out[name] = {
                    'dates':     dates[-n:],
                    'values':    renorm,
                    'lastPrice': d.get('lastPrice'),
                    'pct':       round((renorm[-1] / 100 - 1) * 100, 2),
                }
            return jsonify(out)
        return jsonify({'_loading': True})

    data, _ = db_get_history(period)
    if data:
        return jsonify(data)
    return jsonify({'_loading': True})


@usa_bp.route('/api/history/custom')
def api_history_custom():
    """Özel tarih aralığı için endeks getirileri — cache'deki seriden kesip yeniden normalize eder."""
    start = request.args.get('start', '').strip()
    end   = request.args.get('end',   '').strip()
    if not start or not end or start >= end:
        return jsonify({'error': 'Geçerli start ve end tarihi gerekli (YYYY-MM-DD)'}), 400

    # start'ı kapsayan en uygun seriyi seç (1y günlük → 5y/3y haftalık fallback)
    data = None
    for pk in ('1y', '5y', '3y'):
        d, _ = db_get_history(pk)
        if not d:
            continue
        sample = next(iter(d.values()), {})
        dates = sample.get('dates', [])
        if dates and dates[0] <= start:
            data = d
            break
    if data is None:
        for pk in ('5y', '3y', '1y', '6a'):
            d, _ = db_get_history(pk)
            if d:
                data = d
                break
    if not data:
        return jsonify({'_loading': True})

    result = {}
    for name, d in data.items():
        pairs = [(dt, v) for dt, v in zip(d.get('dates', []), d.get('values', [])) if start <= dt <= end]
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


@usa_bp.route('/api/cache-status')
def api_cache_status():
    data, oldest = db_get_history('1y')
    if not data:
        return jsonify({'ready': False, 'count': 0})
    return jsonify({'ready': True, 'count': len(data),
                    'age_hours': round((time.time() - oldest) / 3600, 1)})


@usa_bp.route('/api/refresh', methods=['POST'])
def api_refresh():
    """collect_usa.py'yi arka planda başlatır (elle tetikleme)."""
    try:
        subprocess.Popen([sys.executable, str(BASE_DIR / 'collect_usa.py')])
        return jsonify({'status': 'collect_usa.py arka planda başlatıldı'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


init_usa_db()
log.info('ABD Endeks Tracker başlatıldı (veri: collect_usa.py / PA scheduled task)')
