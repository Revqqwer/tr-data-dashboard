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
                    session['user_name'] = invite['name'] or username
                    return redirect(url_for('index'))
        return render_template('register.html', error=error, prefill=code)
    return render_template('register.html', error=None, prefill=request.args.get('code',''))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Admin paneli ──────────────────────────────────────────
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
    return render_template('admin.html', codes=codes, users=users, secret=secret)


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


@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('index.html')


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


if __name__ == '__main__':
    app.run(debug=False, port=5000)
