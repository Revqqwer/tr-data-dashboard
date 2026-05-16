from flask import Flask, jsonify, render_template, session, redirect, url_for, request, send_from_directory
import sqlite3, os, secrets, string
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tr-3nfinans-gizli-anahtar-2024')

# ── TEFAS Blueprint ─────────────────────────────────────────
from tefas_api import tefas_bp
app.register_blueprint(tefas_bp)

# ── TEFAS React SPA static dosyalar ────────────────────────
_TEFAS_BUILD = os.path.join(os.path.dirname(__file__), 'tefas_build')

@app.route('/tefas/')
@app.route('/tefas')
def tefas_index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return send_from_directory(_TEFAS_BUILD, 'index.html')

@app.route('/tefas/<path:path>')
def tefas_static(path):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    # Gerçek static dosya mı? (assets/, favicon vb.)
    full = os.path.join(_TEFAS_BUILD, path)
    if os.path.isfile(full):
        return send_from_directory(_TEFAS_BUILD, path)
    # React Router client-side route → index.html döndür
    return send_from_directory(_TEFAS_BUILD, 'index.html')
ADMIN_SECRET = os.environ.get('ADMIN_SECRET', '3n-admin-gizli')

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'cache.db')


def generate_code():
    chars = string.ascii_uppercase + string.digits
    return '3N-' + ''.join(secrets.choice(chars) for _ in range(6))


def init_tables():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS invite_codes (
            code       TEXT PRIMARY KEY,
            name       TEXT,
            active     INTEGER DEFAULT 1,
            created_at TEXT,
            used_by    TEXT
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name          TEXT,
            invite_code   TEXT,
            active        INTEGER DEFAULT 1,
            created_at    TEXT,
            last_login    TEXT
        )''')
        # last_seen kolonu yoksa ekle
        try:
            conn.execute('ALTER TABLE users ADD COLUMN last_seen TEXT')
        except Exception:
            pass
        conn.execute('''CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user  TEXT NOT NULL,
            to_user    TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at    TEXT
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS user_layouts (
            username    TEXT NOT NULL,
            page        TEXT NOT NULL,
            layout_json TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (username, page)
        )''')
init_tables()


def query(sql):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql).fetchall()


def fmt(s):
    """'2026-04-10' → '10-04-2026'"""
    return f'{s[8:10]}-{s[5:7]}-{s[0:4]}'


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            user = conn.execute(
                'SELECT * FROM users WHERE username = ? AND active = 1', (username,)
            ).fetchone()
            if user and check_password_hash(user['password_hash'], password):
                conn.execute('UPDATE users SET last_login = ? WHERE id = ?',
                             (datetime.now().strftime('%Y-%m-%d %H:%M'), user['id']))
                session['logged_in'] = True
                session['username']  = user['username']
                session['user_name'] = user['name'] or user['username']
                return redirect(url_for('index'))
        return render_template('login.html', error=True)
    if session.get('logged_in'):
        return redirect(url_for('index'))
    return render_template('login.html', error=False)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        code      = request.form.get('code', '').strip().upper()
        username  = request.form.get('username', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        error = None
        if not username:
            error = 'Kullanici adi gerekli.'
        elif len(password) < 6:
            error = 'Sifre en az 6 karakter olmali.'
        elif password != password2:
            error = 'Sifreler uyusmuyor.'
        else:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                invite = conn.execute(
                    'SELECT * FROM invite_codes WHERE code=? AND active=1 AND used_by IS NULL',
                    (code,)
                ).fetchone()
                if not invite:
                    error = 'Gecersiz veya kullanilmis davet kodu.'
                elif conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
                    error = 'Bu kullanici adi zaten alinmis, baska bir isim dene.'
                else:
                    now = datetime.now().strftime('%Y-%m-%d %H:%M')
                    conn.execute(
                        'INSERT INTO users (username,password_hash,name,invite_code,created_at) VALUES (?,?,?,?,?)',
                        (username, generate_password_hash(password), invite['name'], code, now)
                    )
                    conn.execute('UPDATE invite_codes SET used_by=? WHERE code=?', (username, code))
                    session['logged_in'] = True
                    session['username']  = username
                    session['user_name'] = invite['name'] or username
                    return redirect(url_for('index'))
        return render_template('register.html', error=error, prefill=code)
    return render_template('register.html', error=None, prefill=request.args.get('code',''))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Admin paneli ──────────────────────────────────────────

TEFAS_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'tefas.db')

def _data_status():
    """Her veri kaynağı için son tarih + kayıt sayısını döndür."""
    items = []

    # ── Makro tablolar (cache.db) ──────────────────────────
    macro_tables = [
        ('dth',           'DTH',                 'EVDS — manuel güncelleme',   'tarih'),
        ('menkul',        'Menkul Kıymetler',    'EVDS — manuel güncelleme',   'tarih'),
        ('credit',        'Krediler',            'EVDS — manuel güncelleme',   'tarih'),
        ('credit_detail', 'Kredi Detayı',        'EVDS — manuel güncelleme',   'tarih'),
        ('butce',         'Bütçe Dengesi',       'EVDS — manuel güncelleme',   'tarih'),
        ('dis_ticaret',   'Dış Ticaret',         'EVDS — manuel güncelleme',   'tarih'),
        ('turizm',        'Turizm',              'EVDS — manuel güncelleme',   'tarih'),
        ('odeme_dengesi', 'Ödemeler Dengesi',    'EVDS — manuel güncelleme',   'tarih'),
        ('konut',         'Konut',               'EVDS — manuel güncelleme',   'tarih'),
        ('enflasyon',     'Enflasyon (TÜFE)',    'EVDS — manuel güncelleme',   'tarih'),
        ('makro',         'Makro Tahmin',        'Manuel güncelleme',          'tarih'),
    ]
    try:
        with sqlite3.connect(DB_PATH) as conn:
            for tbl, label, source, date_col in macro_tables:
                try:
                    row = conn.execute(
                        f'SELECT MAX({date_col}) as last_date, COUNT(*) as cnt FROM {tbl}'
                    ).fetchone()
                    items.append({
                        'label':   label,
                        'source':  source,
                        'db':      'cache.db',
                        'last':    row[0] or '—',
                        'count':   row[1] or 0,
                    })
                except Exception:
                    items.append({'label': label, 'source': source, 'db': 'cache.db', 'last': '—', 'count': 0})
    except Exception:
        pass

    # ── TEFAS tabloları (tefas.db) ────────────────────────
    tefas_tables = [
        ('fund_daily',      'trade_date', 'TEFAS Fon Verisi',    'Otomatik — her gün 17:00 UTC (daily_collect.py)'),
        ('fund_flow',       'trade_date', 'TEFAS Net Akış',      'Otomatik — her gün 17:00 UTC (daily_collect.py)'),
    ]
    try:
        with sqlite3.connect(TEFAS_DB_PATH) as conn:
            for tbl, date_col, label, source in tefas_tables:
                try:
                    row = conn.execute(
                        f'SELECT MAX({date_col}) as last_date, COUNT(*) as cnt FROM {tbl}'
                    ).fetchone()
                    items.append({
                        'label':  label,
                        'source': source,
                        'db':     'tefas.db',
                        'last':   row[0] or '—',
                        'count':  row[1] or 0,
                    })
                except Exception:
                    items.append({'label': label, 'source': source, 'db': 'tefas.db', 'last': '—', 'count': 0})

            # Kripto: BTC ve ETH ayrı
            for asset in ('BTC', 'ETH'):
                try:
                    row = conn.execute(
                        "SELECT MAX(trade_date), COUNT(*) FROM crypto_etf_flow WHERE asset = ?", (asset,)
                    ).fetchone()
                    items.append({
                        'label':  f'{asset} ETF Akışları',
                        'source': 'Otomatik — her gün 18:00 UTC (daily_crypto_collect.py)',
                        'db':     'tefas.db',
                        'last':   row[0] or '—',
                        'count':  row[1] or 0,
                    })
                except Exception:
                    items.append({'label': f'{asset} ETF Akışları', 'source': '', 'db': 'tefas.db', 'last': '—', 'count': 0})
    except Exception:
        pass

    return items


@app.route('/admin/<secret>')
def admin(secret):
    if secret != ADMIN_SECRET:
        return redirect(url_for('login'))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        codes = conn.execute(
            'SELECT code, name, active, created_at, used_by FROM invite_codes ORDER BY created_at DESC'
        ).fetchall()
        users = conn.execute(
            'SELECT id, username, name, active, created_at, last_login FROM users ORDER BY created_at DESC'
        ).fetchall()
    data_status = _data_status()
    return render_template('admin.html', codes=codes, users=users, secret=secret, data_status=data_status)


@app.route('/admin/<secret>/add', methods=['POST'])
def admin_add(secret):
    if secret != ADMIN_SECRET:
        return redirect(url_for('login'))
    name = request.form.get('name', '').strip()
    if name:
        code = generate_code()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                'INSERT INTO invite_codes (code, name, active, created_at) VALUES (?, ?, 1, ?)',
                (code, name, datetime.now().strftime('%Y-%m-%d %H:%M'))
            )
    return redirect(url_for('admin', secret=secret))


@app.route('/admin/<secret>/toggle/<code>')
def admin_toggle(secret, code):
    if secret != ADMIN_SECRET:
        return redirect(url_for('login'))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('UPDATE invite_codes SET active = 1 - active WHERE code = ?', (code,))
    return redirect(url_for('admin', secret=secret))


@app.route('/admin/<secret>/delete/<code>')
def admin_delete(secret, code):
    if secret != ADMIN_SECRET:
        return redirect(url_for('login'))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM invite_codes WHERE code = ?', (code,))
    return redirect(url_for('admin', secret=secret))


@app.route('/admin/<secret>/toggle-user/<int:uid>')
def admin_toggle_user(secret, uid):
    if secret != ADMIN_SECRET:
        return redirect(url_for('login'))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('UPDATE users SET active = 1 - active WHERE id = ?', (uid,))
    return redirect(url_for('admin', secret=secret))


@app.before_request
def _update_last_seen():
    # Eski session'larda username yoksa zorla çıkış yap
    if session.get('logged_in') and not session.get('username'):
        if request.path not in ('/login', '/logout', '/register'):
            session.clear()
            return redirect(url_for('login'))
    if session.get('logged_in') and session.get('username'):
        # Sadece API olmayan sayfa isteklerinde güncelle (performans)
        if not request.path.startswith('/static/'):
            try:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with sqlite3.connect(DB_PATH) as c:
                    c.execute('UPDATE users SET last_seen=? WHERE username=?',
                              (now, session['username']))
            except Exception:
                pass


@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html',
                           username=session.get('username', ''),
                           user_name=session.get('user_name', ''))


_PORTFOLIO_JSON = os.path.join(os.path.dirname(__file__), 'data', 'portfolio.json')

@app.route('/api/portfolio')
def api_portfolio():
    if not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    try:
        with open(_PORTFOLIO_JSON, encoding='utf-8') as f:
            import json as _json
            return _json.load(f)
    except FileNotFoundError:
        return jsonify({'error': 'portfolio data not found'}), 404


# Tickers that require TradingView WebSocket instead of Yahoo Finance
_TV_MAP = {'DMLKTG': 'DMLKT', 'ALTINS': 'ALTIN'}

# In-memory daily cache: { date_str: { ticker: price } }
_LIVE_PRICE_CACHE: dict = {}


@app.route('/api/portfolio/live-prices')
def api_live_prices():
    """Return current BIST prices for open portfolio positions (cached per trading day)."""
    if not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401

    import datetime as _dt
    today = _dt.date.today().isoformat()

    if today in _LIVE_PRICE_CACHE:
        return jsonify(_LIVE_PRICE_CACHE[today])

    # Load open positions from portfolio.json
    try:
        with open(_PORTFOLIO_JSON, encoding='utf-8') as f:
            import json as _json
            pf = _json.load(f)
    except Exception:
        return jsonify({'error': 'portfolio data not found'}), 404

    open_tickers = list(pf.get('open_positions', {}).keys())
    if not open_tickers:
        return jsonify({})

    yf_tickers = [t for t in open_tickers if t not in _TV_MAP]
    tv_tickers  = [t for t in open_tickers if t in _TV_MAP]
    prices: dict = {}

    # ── Yahoo Finance ──────────────────────────────────────────────────────
    if yf_tickers:
        try:
            import yfinance as yf
            import pandas as _pd
            symbols = [f'{t}.IS' for t in yf_tickers]
            df = yf.download(symbols, period='5d', auto_adjust=True, progress=False)
            if not df.empty:
                close = (df['Close'] if isinstance(df.columns, _pd.MultiIndex)
                         else df[['Close']].rename(columns={'Close': symbols[0]}))
                for t in yf_tickers:
                    col = f'{t}.IS'
                    if col in close.columns:
                        s = close[col].dropna()
                        if not s.empty:
                            prices[t] = round(float(s.iloc[-1]), 4)
        except Exception:
            pass

    # ── TradingView WebSocket (sertifikalar not on Yahoo Finance) ──────────
    if tv_tickers:
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(__file__))
            from parse_portfolio import _fetch_tv_ws
            for t in tv_tickers:
                result = _fetch_tv_ws(_TV_MAP[t], 'BIST', n_bars=5)
                if result:
                    prices[t] = result[max(result.keys())]
        except Exception:
            pass

    # Cache result, purge stale entries
    _LIVE_PRICE_CACHE[today] = prices
    for k in [k for k in _LIVE_PRICE_CACHE if k < today]:
        del _LIVE_PRICE_CACHE[k]

    return jsonify(prices)


@app.route('/api/dth')
def dth():
    rows = query('SELECT tarih, bireysel, tuzel, toplam FROM dth ORDER BY tarih')
    return jsonify([{
        'tarih':    fmt(r['tarih']),
        'bireysel': round(r['bireysel'], 3) if r['bireysel'] is not None else None,
        'tuzel':    round(r['tuzel'],    3) if r['tuzel']    is not None else None,
        'toplam':   round(r['toplam'],   3) if r['toplam']   is not None else None,
    } for r in rows])


@app.route('/api/menkul')
def menkul():
    rows = query('SELECT tarih, yil, hisse, dibs FROM menkul ORDER BY tarih')
    return jsonify([{
        'tarih': fmt(r['tarih']),
        'yil':   r['yil'],
        'hisse': round(r['hisse'], 2) if r['hisse'] is not None else None,
        'dibs':  round(r['dibs'],  2) if r['dibs']  is not None else None,
    } for r in rows])


@app.route('/api/credit-detail')
def credit_detail():
    rows = query('''SELECT tarih, konut, tasit, ihtiyac,
                           kk_taksitli, kk_taksitsiz, kk_toplam, kobi
                    FROM credit_detail ORDER BY tarih''')
    return jsonify([{
        'tarih':        fmt(r['tarih']),
        'konut':        round(r['konut'],        2) if r['konut']        is not None else None,
        'tasit':        round(r['tasit'],        2) if r['tasit']        is not None else None,
        'ihtiyac':      round(r['ihtiyac'],      2) if r['ihtiyac']      is not None else None,
        'kk_taksitli':  round(r['kk_taksitli'],  2) if r['kk_taksitli']  is not None else None,
        'kk_taksitsiz': round(r['kk_taksitsiz'], 2) if r['kk_taksitsiz'] is not None else None,
        'kk_toplam':    round(r['kk_toplam'],    2) if r['kk_toplam']    is not None else None,
        'kobi':         round(r['kobi'],         2) if r['kobi']         is not None else None,
    } for r in rows])


@app.route('/api/credit')
def credit():
    rows = query('SELECT tarih, tuketici, ticari, ticari_usd, usdtry FROM credit ORDER BY tarih')
    return jsonify([{
        'tarih':      fmt(r['tarih']),
        'tuketici':   round(r['tuketici'],   2) if r['tuketici']   is not None else None,
        'ticari':     round(r['ticari'],     2) if r['ticari']     is not None else None,
        'ticari_usd': round(r['ticari_usd'], 2) if r['ticari_usd'] is not None else None,
        'usdtry':     round(r['usdtry'],     4) if r['usdtry']     is not None else None,
    } for r in rows])


@app.route('/api/butce')
def butce():
    rows = query('SELECT tarih, gelir, gider, denge, usdtry, nakit_denge, faiz FROM butce ORDER BY tarih')
    return jsonify([{
        'tarih':       fmt(r['tarih']),
        'gelir':       round(r['gelir'],       0) if r['gelir']       is not None else None,
        'gider':       round(r['gider'],       0) if r['gider']       is not None else None,
        'denge':       round(r['denge'],       0) if r['denge']       is not None else None,
        'usdtry':      round(r['usdtry'],      4) if r['usdtry']      is not None else None,
        'nakit_denge': round(r['nakit_denge'], 0) if r['nakit_denge'] is not None else None,
        'faiz':        round(r['faiz'],        0) if r['faiz']        is not None else None,
    } for r in rows])


@app.route('/api/dis-ticaret')
def dis_ticaret():
    rows = query('SELECT tarih, ihracat, ithalat, acik FROM dis_ticaret ORDER BY tarih')
    return jsonify([{
        'tarih':   fmt(r['tarih']),
        'ihracat': round(r['ihracat'], 1) if r['ihracat'] is not None else None,
        'ithalat': round(r['ithalat'], 1) if r['ithalat'] is not None else None,
        'acik':    round(r['acik'],    1) if r['acik']    is not None else None,
    } for r in rows])


@app.route('/api/turizm')
def turizm():
    rows = query('SELECT tarih, gelir, ziyaretci, kisi_basi FROM turizm ORDER BY tarih')
    return jsonify([{
        'tarih':     fmt(r['tarih']),
        'gelir':     round(r['gelir'],     1) if r['gelir']     is not None else None,
        'ziyaretci': round(r['ziyaretci'], 0) if r['ziyaretci'] is not None else None,
        'kisi_basi': round(r['kisi_basi'], 0) if r['kisi_basi'] is not None else None,
    } for r in rows])


@app.route('/api/odeme-dengesi')
def odeme_dengesi():
    rows = query('''SELECT tarih, cari, dis_tic, hizmet, birincil, ikincil,
                           sermaye, net_hata, finans, rezerv, diger_yat, portfoy, dogrudan,
                           portfoy_varlik, portfoy_yukum, dyd_varlik, dyd_yukum
                    FROM odeme_dengesi ORDER BY tarih''')
    def r0(v): return int(round(v, 0)) if v is not None else None
    return jsonify([{
        'tarih':          fmt(r['tarih']),
        'cari':           r0(r['cari']),
        'dis_tic':        r0(r['dis_tic']),
        'hizmet':         r0(r['hizmet']),
        'birincil':       r0(r['birincil']),
        'ikincil':        r0(r['ikincil']),
        'sermaye':        r0(r['sermaye']),
        'net_hata':       r0(r['net_hata']),
        'finans':         r0(r['finans']),
        'rezerv':         r0(r['rezerv']),
        'diger_yat':      r0(r['diger_yat']),
        'portfoy':        r0(r['portfoy']),
        'dogrudan':       r0(r['dogrudan']),
        'portfoy_varlik': r0(r['portfoy_varlik']),
        'portfoy_yukum':  r0(r['portfoy_yukum']),
        'dyd_varlik':     r0(r['dyd_varlik']),
        'dyd_yukum':      r0(r['dyd_yukum']),
    } for r in rows])


@app.route('/api/konut')
def konut():
    rows = query('''SELECT tarih, kfe_tr, kfe_ist, ykfe, yokfe, ykke_tr, ykke_ist,
                           satis_toplam, satis_ipotekli
                    FROM konut ORDER BY tarih''')
    def r2(v): return round(v, 2) if v is not None else None
    def r0(v): return round(v, 0) if v is not None else None
    return jsonify([{
        'tarih':          fmt(r['tarih']),
        'kfe_tr':         r2(r['kfe_tr']),
        'kfe_ist':        r2(r['kfe_ist']),
        'ykfe':           r2(r['ykfe']),
        'yokfe':          r2(r['yokfe']),
        'ykke_tr':        r2(r['ykke_tr']),
        'ykke_ist':       r2(r['ykke_ist']),
        'satis_toplam':   r0(r['satis_toplam']),
        'satis_ipotekli': r0(r['satis_ipotekli']),
    } for r in rows])


@app.route('/api/enflasyon')
def enflasyon():
    rows = query('''SELECT tarih, genel, gida, alkol, giyim, konut, mobilya, saglik,
                           ulasim, bilgi, eglence, egitim, lokanta, sigorta, kisisel
                    FROM enflasyon ORDER BY tarih''')
    return jsonify([{
        'tarih':   fmt(r['tarih']),
        'genel':   round(r['genel'],   4) if r['genel']   is not None else None,
        'gida':    round(r['gida'],    4) if r['gida']    is not None else None,
        'alkol':   round(r['alkol'],   4) if r['alkol']   is not None else None,
        'giyim':   round(r['giyim'],   4) if r['giyim']   is not None else None,
        'konut':   round(r['konut'],   4) if r['konut']   is not None else None,
        'mobilya': round(r['mobilya'], 4) if r['mobilya'] is not None else None,
        'saglik':  round(r['saglik'],  4) if r['saglik']  is not None else None,
        'ulasim':  round(r['ulasim'],  4) if r['ulasim']  is not None else None,
        'bilgi':   round(r['bilgi'],   4) if r['bilgi']   is not None else None,
        'eglence': round(r['eglence'], 4) if r['eglence'] is not None else None,
        'egitim':  round(r['egitim'],  4) if r['egitim']  is not None else None,
        'lokanta': round(r['lokanta'], 4) if r['lokanta'] is not None else None,
        'sigorta': round(r['sigorta'], 4) if r['sigorta'] is not None else None,
        'kisisel': round(r['kisisel'], 4) if r['kisisel'] is not None else None,
    } for r in rows])


@app.route('/api/makro')
def makro():
    rows = list(query('SELECT tarih, sepet, usdtry, pol_faiz FROM makro ORDER BY tarih'))
    result = []
    for i, r in enumerate(rows):
        prev   = rows[i - 1] if i > 0  else None
        prev12 = rows[i - 12] if i >= 12 else None
        sep = r['sepet']; usd = r['usdtry']; pf = r['pol_faiz']
        mom_enf  = round((sep / prev['sepet']   - 1) * 100, 4) if prev   and prev['sepet']   and sep else None
        yoy_enf  = round((sep / prev12['sepet'] - 1) * 100, 4) if prev12 and prev12['sepet'] and sep else None
        mom_kur  = round((usd / prev['usdtry']  - 1) * 100, 4) if prev   and prev['usdtry']  and usd else None
        reel     = round(pf - yoy_enf, 2)  if pf is not None and yoy_enf  is not None else None
        proxy    = round(sep / usd,    2)  if sep and usd else None
        result.append({
            'tarih':     fmt(r['tarih']),
            'sepet':     round(sep, 2) if sep is not None else None,
            'usdtry':    round(usd, 4) if usd is not None else None,
            'pol_faiz':  round(pf,  2) if pf  is not None else None,
            'mom_enf':   mom_enf,
            'yoy_enf':   yoy_enf,
            'mom_kur':   mom_kur,
            'reel_faiz': reel,
            'proxy_kur': proxy,
        })
    return jsonify(result)


# ── Profil ────────────────────────────────────────────────────────────────────

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    # username her zaman session'da olmalı; yoksa yeniden login zorunlu
    me = session.get('username')
    if not me:
        session.clear()
        return redirect(url_for('login'))
    error = success = None
    if request.method == 'POST':
        old_pw  = request.form.get('old_password', '')
        new_pw  = request.form.get('new_password', '')
        new_pw2 = request.form.get('new_password2', '')
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            user = conn.execute('SELECT * FROM users WHERE username=?', (me,)).fetchone()
            if not user:
                session.clear(); return redirect(url_for('login'))
            if not check_password_hash(user['password_hash'], old_pw):
                error = 'Mevcut şifre yanlış.'
            elif len(new_pw) < 6:
                error = 'Yeni şifre en az 6 karakter olmalı.'
            elif new_pw != new_pw2:
                error = 'Şifreler uyuşmuyor.'
            else:
                conn.execute('UPDATE users SET password_hash=? WHERE username=?',
                             (generate_password_hash(new_pw), me))
                success = 'Şifre güncellendi.'
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute('SELECT * FROM users WHERE username=?', (me,)).fetchone()
    if not user:
        session.clear(); return redirect(url_for('login'))
    return render_template('profile.html', user=dict(user), error=error, success=success)


# ── Chat API ──────────────────────────────────────────────────────────────────

@app.route('/api/chat/online')
def api_chat_online():
    if not session.get('logged_in'):
        return jsonify([])
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        users = conn.execute(
            'SELECT username, name FROM users WHERE last_seen >= ? AND active = 1',
            (cutoff,)
        ).fetchall()
    me = session.get('username', '')
    return jsonify([
        {'username': u['username'], 'name': u['name'] or u['username']}
        for u in users
    ])


@app.route('/api/chat/messages')
def api_chat_messages():
    if not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    me    = session.get('username', '')
    other = request.args.get('with', '')
    after = int(request.args.get('after', 0))
    if not other:
        return jsonify([])
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        msgs = conn.execute('''
            SELECT id, from_user, to_user, content, created_at, read_at
            FROM messages
            WHERE id > ?
              AND ((from_user=? AND to_user=?) OR (from_user=? AND to_user=?))
            ORDER BY created_at ASC LIMIT 200
        ''', (after, me, other, other, me)).fetchall()
    return jsonify([dict(m) for m in msgs])


@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    if not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    me   = session.get('username', '')
    data = request.json or {}
    to      = data.get('to', '').strip()
    content = data.get('content', '').strip()
    if not to or not content:
        return jsonify({'error': 'missing fields'}), 400
    if len(content) > 2000:
        return jsonify({'error': 'too long'}), 400
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('INSERT INTO messages (from_user,to_user,content,created_at) VALUES (?,?,?,?)',
                     (me, to, content, now))
        row = conn.execute('SELECT last_insert_rowid()').fetchone()
        msg_id = row[0]
    return jsonify({'id': msg_id, 'from_user': me, 'to_user': to,
                    'content': content, 'created_at': now})


@app.route('/api/chat/mark-read', methods=['POST'])
def api_chat_mark_read():
    if not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    me    = session.get('username', '')
    other = (request.json or {}).get('with', '')
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''UPDATE messages SET read_at=?
                        WHERE to_user=? AND from_user=? AND read_at IS NULL''',
                     (now, me, other))
    return jsonify({'ok': True})


@app.route('/api/chat/unread')
def api_chat_unread():
    if not session.get('logged_in'):
        return jsonify({})
    me = session.get('username', '')
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''
            SELECT from_user, COUNT(*) as cnt FROM messages
            WHERE to_user=? AND read_at IS NULL
            GROUP BY from_user
        ''', (me,)).fetchall()
    return jsonify({r['from_user']: r['cnt'] for r in rows})


# ── Layout API ────────────────────────────────────────────────────────────────

@app.route('/api/layout/<page>', methods=['GET', 'POST'])
def api_layout(page):
    if not session.get('logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    me = session.get('username', '')
    if not me:
        return jsonify({'error': 'no username in session'}), 400
    if request.method == 'GET':
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                'SELECT layout_json FROM user_layouts WHERE username=? AND page=?',
                (me, page)
            ).fetchone()
        return jsonify({'layout': row[0] if row else None})
    layout_json = request.json.get('layout', '{}') if request.json else '{}'
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''INSERT OR REPLACE INTO user_layouts (username,page,layout_json,updated_at)
                        VALUES (?,?,?,?)''', (me, page, layout_json, now))
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(debug=False, port=5000)
