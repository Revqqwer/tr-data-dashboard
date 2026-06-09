from flask import Flask, jsonify, render_template, session, redirect, url_for, request, send_from_directory
import sqlite3, os, secrets, string, smtplib, random
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.security import generate_password_hash, check_password_hash
import requests as _http
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)

# ── Mail ayarları ─────────────────────────────────────────────
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')

def send_email(to_addr: str, subject: str, body: str) -> bool:
    """Gmail SMTP ile email gönder."""
    try:
        msg = MIMEMultipart()
        msg['From']    = MAIL_USERNAME
        msg['To']      = to_addr
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15) as s:
            s.login(MAIL_USERNAME, MAIL_PASSWORD)
            s.sendmail(MAIL_USERNAME, to_addr, msg.as_string())
        return True
    except Exception as e:
        print(f'Email gönderilemedi: {e}')
        return False

# ── Discord OAuth2 ───────────────────────────────────────────
DISCORD_CLIENT_ID     = os.environ.get('DISCORD_CLIENT_ID',     '1505961330732044308')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET', '')
DISCORD_REDIRECT_URI  = os.environ.get('DISCORD_REDIRECT_URI',  'https://www.3nfinans.com/auth/discord/callback')
DISCORD_GUILD_ID      = os.environ.get('DISCORD_GUILD_ID',      '1119373930885546087')
DISCORD_ROLE_ID       = os.environ.get('DISCORD_ROLE_ID',       '1196022785114378380')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tr-3nfinans-gizli-anahtar-2024')

# Analytics IDs (isteğe bağlı — .env'e ekle)
if os.environ.get('GA4_ID'):
    app.config['GA4_ID'] = os.environ['GA4_ID']
if os.environ.get('CLARITY_ID'):
    app.config['CLARITY_ID'] = os.environ['CLARITY_ID']

# ── TEFAS Blueprint ─────────────────────────────────────────
from tefas_api import tefas_bp
app.register_blueprint(tefas_bp)

# ── BİST Tracker Blueprint ──────────────────────────────────
from bist_api import bist_bp
app.register_blueprint(bist_bp)

# ── TEFAS React SPA static dosyalar ────────────────────────
_TEFAS_BUILD = os.path.join(os.path.dirname(__file__), 'tefas_build')

@app.route('/tefas/')
@app.route('/tefas')
def tefas_index():
    return send_from_directory(_TEFAS_BUILD, 'index.html')

@app.route('/tefas/<path:path>')
def tefas_static(path):
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
        # Ek kolonlar yoksa ekle
        for col in ('last_seen TEXT', 'discord_id TEXT', 'email TEXT'):
            try:
                conn.execute(f'ALTER TABLE users ADD COLUMN {col}')
            except Exception:
                pass
        # Şifre sıfırlama tokenları
        conn.execute('''CREATE TABLE IF NOT EXISTS password_reset (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used       INTEGER DEFAULT 0
        )''')
        # Rapor e-posta aboneleri
        conn.execute('''CREATE TABLE IF NOT EXISTS report_subscribers (
            email      TEXT PRIMARY KEY,
            token      TEXT NOT NULL,
            confirmed  INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )''')
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
        conn.execute('''CREATE TABLE IF NOT EXISTS tr_yield_cache (
            ym    TEXT PRIMARY KEY,
            tr2y  REAL,
            tr10y REAL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ab_surplus (
            tarih   TEXT PRIMARY KEY,
            a02     REAL,
            a10     REAL,
            usd_try REAL,
            deger   REAL
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
        email     = request.form.get('email', '').strip().lower()
        username  = request.form.get('username', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        error = None
        if not email or '@' not in email:
            error = 'Geçerli bir email adresi girin.'
        elif not username or len(username) < 3:
            error = 'Kullanıcı adı en az 3 karakter olmalı.'
        elif len(password) < 6:
            error = 'Şifre en az 6 karakter olmalı.'
        elif password != password2:
            error = 'Şifreler uyuşmuyor.'
        else:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                if conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
                    error = 'Bu kullanıcı adı alınmış, başka bir isim dene.'
                elif conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
                    error = 'Bu email adresi zaten kayıtlı.'
                else:
                    now = datetime.now().strftime('%Y-%m-%d %H:%M')
                    conn.execute(
                        'INSERT INTO users (username,password_hash,name,email,created_at) VALUES (?,?,?,?,?)',
                        (username, generate_password_hash(password), username, email, now)
                    )
                    session['logged_in'] = True
                    session['username']  = username
                    session['user_name'] = username
                    return redirect(url_for('dashboard'))
        return render_template('register.html', error=error)
    return render_template('register.html', error=None)


# ── Rapor E-posta Aboneliği ───────────────────────────────────────────────────

@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    email = (request.json or {}).get('email', '').strip().lower()
    if not email or '@' not in email:
        return jsonify({'ok': False, 'error': 'Geçerli bir email adresi girin.'})
    token = secrets.token_urlsafe(24)
    now   = datetime.now().strftime('%Y-%m-%d %H:%M')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT confirmed FROM report_subscribers WHERE email=?', (email,)).fetchone()
        if existing and existing[0]:
            return jsonify({'ok': False, 'error': 'Bu email zaten abone.'})
        conn.execute(
            'INSERT OR REPLACE INTO report_subscribers (email, token, confirmed, created_at) VALUES (?,?,0,?)',
            (email, token, now)
        )
    confirm_url = f'https://www.3nfinans.com/confirm-subscription/{token}'
    body = f'''
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px">
      <h2 style="color:#f0b429">3N Finans — Sabah Raporu Aboneliği</h2>
      <p>Her sabah günlük piyasa özetini emailinize almak için aşağıdaki butona tıklayın.</p>
      <div style="margin:24px 0">
        <a href="{confirm_url}" style="display:inline-block;padding:14px 28px;
           background:#f0b429;color:#050a14;font-weight:700;border-radius:8px;text-decoration:none;">
          Aboneliği Onayla
        </a>
      </div>
      <p style="color:#666;font-size:12px">Bu emaili siz istemediyseniz görmezden gelebilirsiniz.</p>
    </div>'''
    ok = send_email(email, '3N Finans — Sabah Raporu Aboneliğini Onayla', body)
    if ok:
        return jsonify({'ok': True, 'msg': 'Onay emaili gönderildi. Gelen kutunuzu kontrol edin.'})
    return jsonify({'ok': False, 'error': 'Email gönderilemedi. Lütfen tekrar deneyin.'})


@app.route('/confirm-subscription/<token>')
def confirm_subscription(token):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute('SELECT email FROM report_subscribers WHERE token=?', (token,)).fetchone()
        if not row:
            return '<h2>Geçersiz veya süresi dolmuş bağlantı.</h2>', 400
        conn.execute('UPDATE report_subscribers SET confirmed=1 WHERE token=?', (token,))
        email = row[0]
    body = f'''
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px">
      <h2 style="color:#f0b429">3N Finans</h2>
      <h3>✅ Aboneliğiniz onaylandı!</h3>
      <p>Her sabah günlük piyasa raporunu <strong>{email}</strong> adresinize göndereceğiz.</p>
      <div style="margin:20px 0">
        <a href="https://www.3nfinans.com/dashboard" style="color:#f0b429">Panele Git →</a>
      </div>
    </div>'''
    send_email(email, '3N Finans — Aboneliğiniz Onaylandı', body)
    return render_template('subscription_confirmed.html')


@app.route('/unsubscribe/<token>')
def unsubscribe(token):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM report_subscribers WHERE token=?', (token,))
    return '<div style="font-family:sans-serif;max-width:400px;margin:60px auto;text-align:center"><h2>Aboneliğiniz iptal edildi.</h2><p><a href="/">Ana Sayfaya Dön</a></p></div>'


@app.route('/api/subscribers/count')
def api_subscribers_count():
    with sqlite3.connect(DB_PATH) as conn:
        n = conn.execute('SELECT COUNT(*) FROM report_subscribers WHERE confirmed=1').fetchone()[0]
    return jsonify({'count': n})


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    msg = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        with sqlite3.connect(DB_PATH) as conn:
            user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user:
            code = str(random.randint(100000, 999999))
            expires = (datetime.now() + timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M')
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute('DELETE FROM password_reset WHERE email=?', (email,))
                conn.execute('INSERT INTO password_reset (token,email,expires_at) VALUES (?,?,?)',
                             (code, email, expires))
            body = f'''
            <div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:24px">
              <h2 style="color:#f0b429">3N Finans — Şifre Sıfırlama</h2>
              <p>Şifre sıfırlama kodunuz:</p>
              <div style="font-size:36px;font-weight:bold;letter-spacing:8px;text-align:center;
                          padding:20px;background:#f5f5f5;border-radius:8px;margin:16px 0">{code}</div>
              <p style="color:#666;font-size:13px">Bu kod 30 dakika geçerlidir.</p>
            </div>'''
            ok = send_email(email, '3N Finans — Şifre Sıfırlama Kodu', body)
        # Güvenlik: kullanıcı var olsa da olmasa da aynı mesajı göster
        msg = 'Email adresiniz kayıtlıysa sıfırlama kodu gönderildi. Gelen kutunuzu kontrol edin.'
    return render_template('forgot_password.html', msg=msg)


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    error = None
    if request.method == 'POST':
        email     = request.form.get('email', '').strip().lower()
        code      = request.form.get('code', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        if len(password) < 6:
            error = 'Şifre en az 6 karakter olmalı.'
        elif password != password2:
            error = 'Şifreler uyuşmuyor.'
        else:
            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    'SELECT * FROM password_reset WHERE token=? AND email=? AND used=0 AND expires_at>=?',
                    (code, email, now)
                ).fetchone()
                if not row:
                    error = 'Kod geçersiz veya süresi dolmuş.'
                else:
                    conn.execute('UPDATE users SET password_hash=? WHERE email=?',
                                 (generate_password_hash(password), email))
                    conn.execute('UPDATE password_reset SET used=1 WHERE token=?', (code,))
                    return redirect(url_for('login') + '?reset=1')
    return render_template('reset_password.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Discord OAuth2 ────────────────────────────────────────────────────────────

@app.route('/auth/discord')
def discord_login():
    import urllib.parse
    params = {
        'client_id':     DISCORD_CLIENT_ID,
        'redirect_uri':  DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'scope':         'identify guilds.members.read',
    }
    return redirect('https://discord.com/api/oauth2/authorize?' + urllib.parse.urlencode(params))


@app.route('/auth/discord/callback')
def discord_callback():
    code = request.args.get('code')
    if not code:
        return render_template('login.html', error=False,
                               discord_error='Discord girişi iptal edildi.')

    # 1. Kodu access token ile değiştir
    token_res = _http.post('https://discord.com/api/oauth2/token', data={
        'client_id':     DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type':    'authorization_code',
        'code':          code,
        'redirect_uri':  DISCORD_REDIRECT_URI,
    }, headers={'Content-Type': 'application/x-www-form-urlencoded'})

    if token_res.status_code != 200:
        return render_template('login.html', error=False,
                               discord_error='Discord bağlantısı başarısız, tekrar dene.')

    access_token = token_res.json().get('access_token')

    # 2. Discord kullanıcı bilgilerini al
    user_res = _http.get('https://discord.com/api/users/@me',
                         headers={'Authorization': f'Bearer {access_token}'})
    if user_res.status_code != 200:
        return render_template('login.html', error=False,
                               discord_error='Kullanıcı bilgisi alınamadı.')
    discord_user = user_res.json()
    discord_id   = discord_user['id']

    # 3. Sunucu üyeliği ve rol kontrolü
    member_res = _http.get(
        f'https://discord.com/api/users/@me/guilds/{DISCORD_GUILD_ID}/member',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    if member_res.status_code != 200:
        return render_template('login.html', error=False,
                               discord_error='Bu Discord sunucusunun üyesi değilsin.')

    member = member_res.json()
    if DISCORD_ROLE_ID not in member.get('roles', []):
        return render_template('login.html', error=False,
                               discord_error='Gerekli role sahip değilsin, erişim reddedildi.')

    # 4. Kullanıcıyı bul ya da ilk kurulum akışına yönlendir
    import re as _re
    display_name = (member.get('nick')
                    or discord_user.get('global_name')
                    or discord_user.get('username', ''))

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute('SELECT * FROM users WHERE discord_id=?', (discord_id,)).fetchone()

    if not user:
        # İlk giriş — şifre belirleme sayfasına yönlendir
        raw = _re.sub(r'[^a-z0-9_]', '',
                      display_name.lower().replace(' ', '_'))
        suggested = raw[:28] if raw else 'user'
        # Çakışma kontrolü
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            base, suffix = suggested, 2
            while conn.execute('SELECT id FROM users WHERE username=?', (suggested,)).fetchone():
                suggested = f'{base}_{suffix}'; suffix += 1
        session['discord_pending'] = {
            'discord_id':   discord_id,
            'display_name': display_name,
            'username':     suggested,
        }
        return redirect(url_for('discord_setup'))

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('UPDATE users SET last_login=? WHERE id=?', (now, user['id']))

    session['logged_in'] = True
    session['username']  = user['username']
    session['user_name'] = user['name'] or user['username']
    return redirect(url_for('index'))


@app.route('/auth/discord/setup', methods=['GET', 'POST'])
def discord_setup():
    pending = session.get('discord_pending')
    if not pending:
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        username  = request.form.get('username', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        import re as _re
        if not username or not _re.match(r'^[a-z0-9_]{3,30}$', username):
            error = 'Kullanıcı adı 3-30 karakter, sadece harf/rakam/alt çizgi olmalı.'
        elif len(password) < 6:
            error = 'Şifre en az 6 karakter olmalı.'
        elif password != password2:
            error = 'Şifreler uyuşmuyor.'
        else:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                if conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
                    error = 'Bu kullanıcı adı alınmış, başka bir isim dene.'
                else:
                    now = datetime.now().strftime('%Y-%m-%d %H:%M')
                    conn.execute(
                        'INSERT INTO users (username, password_hash, name, active, created_at, discord_id) '
                        'VALUES (?,?,?,1,?,?)',
                        (username, generate_password_hash(password),
                         pending['display_name'], now, pending['discord_id'])
                    )
                    session.pop('discord_pending', None)
                    session['logged_in'] = True
                    session['username']  = username
                    session['user_name'] = pending['display_name'] or username
                    return redirect(url_for('index'))
    return render_template('discord_setup.html',
                           suggested=pending['username'],
                           display_name=pending['display_name'],
                           error=error)


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
        ('ab_surplus',    'TCMB Rezervleri',     'EVDS — manuel güncelleme',   'tarih'),
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
    # Market brief raporları
    try:
        from tefas_backend.market_agent.reports import get_reports
        market_reports = get_reports(limit=50)
    except Exception:
        market_reports = []
    return render_template('admin.html', codes=codes, users=users, secret=secret,
                           data_status=data_status, market_reports=market_reports)


@app.route('/admin/<secret>/market-brief/<report_id>/delete', methods=['POST'])
def admin_delete_market_brief(secret, report_id):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'unauthorized'}), 403
    try:
        from tefas_backend.market_agent.reports import delete_by_id
        delete_by_id(report_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


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
def _www_redirect():
    """3nfinans.com → www.3nfinans.com yönlendir (301)"""
    if request.host == '3nfinans.com':
        return redirect('https://www.3nfinans.com' + request.full_path.rstrip('?'), 301)


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
    # Giriş yapmamış kullanıcılara landing page göster
    if not session.get('logged_in'):
        return render_template('landing.html')
    return render_template('index.html',
                           username=session.get('username', ''),
                           user_name=session.get('user_name', ''),
                           logged_in=session.get('logged_in', False))


@app.route('/sitemap.xml')
def sitemap():
    from flask import Response
    from datetime import date
    today = date.today().isoformat()
    pages = [
        ('/', '1.0', 'daily'),
        ('/dashboard', '0.9', 'daily'),
        ('/register', '0.7', 'monthly'),
        ('/login', '0.6', 'monthly'),
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for loc, pri, freq in pages:
        xml += f'  <url>\n'
        xml += f'    <loc>https://www.3nfinans.com{loc}</loc>\n'
        xml += f'    <lastmod>{today}</lastmod>\n'
        xml += f'    <changefreq>{freq}</changefreq>\n'
        xml += f'    <priority>{pri}</priority>\n'
        xml += f'  </url>\n'
    xml += '</urlset>'
    return Response(xml, mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    from flask import Response
    txt = "User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /api/\nSitemap: https://www.3nfinans.com/sitemap.xml\n"
    return Response(txt, mimetype='text/plain')


@app.route('/dashboard')
def dashboard():
    return render_template('index.html',
                           username=session.get('username', ''),
                           user_name=session.get('user_name', ''),
                           logged_in=session.get('logged_in', False))


_PORTFOLIO_JSON      = os.path.join(os.path.dirname(__file__), 'data', 'portfolio.json')
_PORTFOLIO_OVERRIDES = os.path.join(os.path.dirname(__file__), 'data', 'portfolio_overrides.json')
_MAKRO_FORECAST_JSON = os.path.join(os.path.dirname(__file__), 'data', 'makro_forecast.json')

import json as _json


def _load_makro_forecast() -> list:
    try:
        with open(_MAKRO_FORECAST_JSON, encoding='utf-8') as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return []


def _save_makro_forecast(rows: list):
    os.makedirs(os.path.dirname(_MAKRO_FORECAST_JSON), exist_ok=True)
    with open(_MAKRO_FORECAST_JSON, 'w', encoding='utf-8') as f:
        _json.dump(rows, f, ensure_ascii=False, indent=2)

def _load_overrides() -> dict:
    try:
        with open(_PORTFOLIO_OVERRIDES, encoding='utf-8') as f:
            return _json.load(f)
    except FileNotFoundError:
        return {'open_positions': {}}

def _save_overrides(ov: dict):
    os.makedirs(os.path.dirname(_PORTFOLIO_OVERRIDES), exist_ok=True)
    with open(_PORTFOLIO_OVERRIDES, 'w', encoding='utf-8') as f:
        _json.dump(ov, f, ensure_ascii=False, indent=2)

def _portfolio_with_overrides() -> dict | None:
    """Load portfolio.json then apply manual overrides (positions, NSP, cash)."""
    try:
        with open(_PORTFOLIO_JSON, encoding='utf-8') as f:
            pf = _json.load(f)
    except FileNotFoundError:
        return None
    ov = _load_overrides()

    # ── Pozisyon override'ları ──────────────────────────────────────────────
    ov_pos = ov.get('open_positions', {})
    if ov_pos:
        base = dict(pf.get('open_positions', {}))
        for ticker, op in ov_pos.items():
            qty = float(op.get('qty', 0))
            if qty > 0:
                avg = float(op.get('avg_cost', 0))
                base[ticker] = {'qty': qty, 'avg_cost': avg, 'cost_basis': round(qty * avg, 2)}
            else:
                base.pop(ticker, None)
        pf['open_positions'] = base

    # ── NSP birim override'ı ────────────────────────────────────────────────
    nsp_units_ov = ov.get('nsp_units_override')
    if nsp_units_ov is not None:
        nsp_units_ov = float(nsp_units_ov)
        pf['nsp_current_units'] = nsp_units_ov
        nsp_dv = pf.get('nsp_daily_value', [])
        last_price = nsp_dv[-1]['price'] if nsp_dv else 1.0
        pf['nsp_current_value'] = round(nsp_units_ov * last_price, 2)

    # ── Nakit (cash) override'ı ────────────────────────────────────────────
    cash_ov = ov.get('cash_value_override')
    if cash_ov is not None:
        pdv = pf.get('portfolio_daily_value', [])
        if pdv:
            pdv[-1]['cash_value'] = round(float(cash_ov), 2)

    # ── Manuel kapatılan pozisyonları pnl_by_ticker'a yansıt ───────────────
    for cp in ov.get('closed_positions', []):
        t         = cp['ticker']
        qty_sold  = float(cp['qty'])
        proceeds  = float(cp['proceeds'])
        avg_cost  = float(cp.get('avg_cost', 0))
        pnl_map   = pf.setdefault('pnl_by_ticker', {})
        if t not in pnl_map:
            pnl_map[t] = {
                'buy_qty': qty_sold, 'buy_amount': round(qty_sold * avg_cost, 2),
                'sell_qty': 0.0, 'sell_amount': 0.0,
                'realized_pnl': 0.0, 'avg_buy': avg_cost,
                'avg_sell': 0.0, 'pnl_pct': 0.0,
            }
        e = pnl_map[t]
        e['sell_qty']    = round(e.get('sell_qty', 0) + qty_sold, 4)
        e['sell_amount'] = round(e.get('sell_amount', 0) + proceeds, 2)
        e['avg_sell']    = round(e['sell_amount'] / e['sell_qty'], 4) if e['sell_qty'] else 0.0
        cost_base        = e['sell_qty'] * e.get('avg_buy', 0)
        e['realized_pnl'] = round(e['sell_amount'] - cost_base, 2)
        e['pnl_pct']      = round(e['realized_pnl'] / cost_base * 100, 2) if cost_base else 0.0

    return pf

@app.route('/api/portfolio')
def api_portfolio():
    pf = _portfolio_with_overrides()
    if pf is None:
        return jsonify({'error': 'portfolio data not found'}), 404
    return jsonify(pf)


# Tickers that require TradingView WebSocket instead of Yahoo Finance
_TV_MAP = {'DMLKTG': 'DMLKT', 'ALTINS': 'ALTIN'}

# In-memory daily cache: { date_str: { ticker: price } }
_LIVE_PRICE_CACHE: dict = {}


@app.route('/api/portfolio/live-prices')
def api_live_prices():
    """Return current BIST prices for open portfolio positions (cached per trading day)."""
    import datetime as _dt
    today = _dt.date.today().isoformat()

    if today in _LIVE_PRICE_CACHE:
        return jsonify(_LIVE_PRICE_CACHE[today])

    # Load open positions (with overrides applied)
    pf = _portfolio_with_overrides()
    if pf is None:
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



@app.route('/admin/<secret>/portfolio-clear-price-cache', methods=['POST'])
def admin_clear_price_cache(secret):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    _LIVE_PRICE_CACHE.clear()
    return jsonify({'ok': True})

# ── Portföy Override Admin Endpoint'leri ────────────────────────────────────

@app.route('/admin/<secret>/portfolio-overrides')
def admin_portfolio_overrides_get(secret):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    # Base (raw PDF) portfolio
    try:
        with open(_PORTFOLIO_JSON, encoding='utf-8') as f:
            pf_base = _json.load(f)
    except Exception:
        pf_base = {}
    # Merged portfolio (overrides applied) — use this for current effective values
    pf = _portfolio_with_overrides() or {}
    ov = _load_overrides()

    # Last NSP unit price (for unit↔TL conversion in UI)
    nsp_dv = pf_base.get('nsp_daily_value', [])
    nsp_last_price = nsp_dv[-1]['price'] if nsp_dv else 1.0

    # Current effective cash value (after cash override if any)
    pdv = pf.get('portfolio_daily_value', [])
    cash_value = pdv[-1]['cash_value'] if pdv else 0.0

    return jsonify({
        'base_positions':    pf_base.get('open_positions', {}),
        'nsp_current_units': pf.get('nsp_current_units', 0),
        'nsp_current_value': pf.get('nsp_current_value', 0),
        'nsp_last_price':    round(nsp_last_price, 6),
        'cash_value':        round(cash_value, 2),
        'overrides':         ov,
        'closed_positions':  ov.get('closed_positions', []),
    })


@app.route('/admin/<secret>/portfolio-override', methods=['POST'])
def admin_portfolio_override_set(secret):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    data   = request.get_json(silent=True) or {}
    ticker = data.get('ticker', '').strip().upper()
    qty    = data.get('qty')
    avg    = data.get('avg_cost')
    if not ticker:
        return jsonify({'error': 'ticker required'}), 400
    try:
        qty = float(qty)
        avg = float(avg)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid qty or avg_cost'}), 400

    ov = _load_overrides()

    # ── Pozisyon override ──
    # qty=0 → override'ı silme, qty:0 olarak sakla; _portfolio_with_overrides()
    # bunu görünce base'den de siler. Gerçek reset için /delete endpoint'i var.
    if qty <= 0:
        ov['open_positions'][ticker] = {'qty': 0, 'avg_cost': 0}
    else:
        ov['open_positions'][ticker] = {'qty': qty, 'avg_cost': round(avg, 4)}

    # ── NSP birim override (opsiyonel, frontend tarafından hesaplanır) ──
    nsp_ov = data.get('nsp_units_override')
    if nsp_ov is not None:
        try:
            ov['nsp_units_override'] = round(float(nsp_ov), 4)
        except (TypeError, ValueError):
            pass

    # ── Nakit override (opsiyonel) ──
    cash_ov = data.get('cash_value_override')
    if cash_ov is not None:
        try:
            ov['cash_value_override'] = round(float(cash_ov), 2)
        except (TypeError, ValueError):
            pass

    _save_overrides(ov)
    return jsonify({'ok': True})


@app.route('/admin/<secret>/portfolio-override/<ticker>/delete', methods=['POST'])
def admin_portfolio_override_delete(secret, ticker):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    ov = _load_overrides()
    ov['open_positions'].pop(ticker.upper(), None)
    # Tüm pozisyon override'ları kalktıysa NSP/cash override'larını da temizle
    data = request.get_json(silent=True) or {}
    if data.get('clear_funding'):
        ov.pop('nsp_units_override', None)
        ov.pop('cash_value_override', None)
    _save_overrides(ov)
    return jsonify({'ok': True})


@app.route('/admin/<secret>/portfolio-reset-funding', methods=['POST'])
def admin_portfolio_reset_funding(secret):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    ov = _load_overrides()
    ov.pop('nsp_units_override', None)
    ov.pop('cash_value_override', None)
    _save_overrides(ov)
    return jsonify({'ok': True})


@app.route('/admin/<secret>/portfolio-set-cash', methods=['POST'])
def admin_portfolio_set_cash(secret):
    """Nakit (cash_value_override) değerini doğrudan kaydet."""
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    val  = data.get('cash_value')
    if val is None:
        return jsonify({'error': 'cash_value required'}), 400
    try:
        val = round(float(val), 2)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid cash_value'}), 400
    ov = _load_overrides()
    if val == 0:
        ov.pop('cash_value_override', None)   # 0 → override'ı kaldır
    else:
        ov['cash_value_override'] = val
    _save_overrides(ov)
    return jsonify({'ok': True})


@app.route('/admin/<secret>/portfolio-close-position', methods=['POST'])
def admin_portfolio_close_position(secret):
    """Bir hisse pozisyonunu güncel fiyattan kapat, nakite ekle, geçmişe yaz."""
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'forbidden'}), 403
    import datetime as _dt
    data = request.get_json(silent=True) or {}
    ticker   = data.get('ticker', '').strip().upper()
    price_in = data.get('price')           # opsiyonel; yoksa cache / last_prices
    if not ticker:
        return jsonify({'error': 'ticker required'}), 400

    pf = _portfolio_with_overrides()
    if pf is None:
        return jsonify({'error': 'portfolio data not found'}), 404

    pos = pf.get('open_positions', {}).get(ticker)
    if not pos or float(pos.get('qty', 0)) <= 0:
        return jsonify({'error': f'{ticker} pozisyonu bulunamadi'}), 400

    qty      = float(pos['qty'])
    avg_cost = float(pos.get('avg_cost', 0))

    # ── Fiyat bul ───────────────────────────────────────────────────────────
    if price_in is not None:
        price = round(float(price_in), 4)
    else:
        today = _dt.date.today().isoformat()
        live  = _LIVE_PRICE_CACHE.get(today, {})
        price = live.get(ticker) or pf.get('last_prices', {}).get(ticker)
    if price is None:
        return jsonify({'error': f'{ticker} icin fiyat bulunamadi, fiyati manuel girin'}), 400
    price    = round(float(price), 4)
    proceeds = round(qty * price, 2)
    realized = round(proceeds - qty * avg_cost, 2)

    # ── Override dosyasını güncelle ─────────────────────────────────────────
    ov = _load_overrides()
    # 1) Pozisyonu sıfırla (qty=0 → open_positions'dan kaldırılacak)
    ov.setdefault('open_positions', {})[ticker] = {'qty': 0, 'avg_cost': 0}
    # 2) Nakiti artır
    pdv      = pf.get('portfolio_daily_value', [])
    base_cash = pdv[-1]['cash_value'] if pdv else 0.0
    cur_cash  = ov.get('cash_value_override', base_cash)
    ov['cash_value_override'] = round(float(cur_cash) + proceeds, 2)
    # 3) Kapatılan pozisyonu kaydet
    ov.setdefault('closed_positions', []).append({
        'ticker':       ticker,
        'qty':          qty,
        'price':        price,
        'proceeds':     proceeds,
        'avg_cost':     avg_cost,
        'realized_pnl': realized,
        'date':         _dt.date.today().isoformat(),
    })
    _save_overrides(ov)
    _LIVE_PRICE_CACHE.clear()

    return jsonify({
        'ok':          True,
        'ticker':      ticker,
        'qty':         qty,
        'price':       price,
        'proceeds':    proceeds,
        'realized_pnl': realized,
        'new_cash':    ov['cash_value_override'],
    })


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


@app.route('/api/tcmb-ab')
def tcmb_ab():
    rows = query('SELECT tarih, a02, a10, usd_try, deger FROM ab_surplus ORDER BY tarih')
    def r2(v): return round(v, 2) if v is not None else None
    def r4(v): return round(v, 4) if v is not None else None
    return jsonify([{
        'tarih':   fmt(r['tarih']),
        'a02':     r2(r['a02']),
        'a10':     r2(r['a10']),
        'usd_try': r4(r['usd_try']),
        'deger':   r2(r['deger']),
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


@app.route('/api/makro-forecast')
def api_makro_forecast():
    """3N Finans makro tahminleri — herkese açık."""
    return jsonify(_load_makro_forecast())


@app.route('/admin/<secret>/makro-forecast', methods=['GET', 'POST'])
def admin_makro_forecast(secret):
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'Forbidden'}), 403
    if request.method == 'GET':
        return jsonify({'rows': _load_makro_forecast()})
    data = request.get_json(force=True)
    rows = data.get('rows', [])
    # Normalize: her satır {mom_enf, mom_kur, pol_faiz, tr2y, tr10y, note}
    clean = []
    for r in rows:
        clean.append({
            'mom_enf':  r.get('mom_enf',  ''),
            'mom_kur':  r.get('mom_kur',  ''),
            'pol_faiz': r.get('pol_faiz', ''),
            'tr2y':     r.get('tr2y',     ''),
            'tr10y':    r.get('tr10y',    ''),
            'note':     r.get('note',     ''),
        })
    _save_makro_forecast(clean)
    return jsonify({'ok': True})


@app.route('/admin/<secret>/scrape-categories', methods=['POST'])
def admin_scrape_categories(secret):
    """Şemsiye fon kategorilerini TEFAS'tan çek ve fund_meta tablosuna yaz."""
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        from tefas_backend.category_scraper import scrape_categories, category_stats
        force = request.json.get('force', False) if request.is_json else False
        result = scrape_categories(dry_run=False, force=force)
        stats = category_stats()
        return jsonify({'ok': True, 'result': result, 'stats': stats})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/admin/<secret>/scrape-categories/stats', methods=['GET'])
def admin_category_stats(secret):
    """Mevcut kategori istatistiklerini döner."""
    if secret != ADMIN_SECRET:
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        from tefas_backend.category_scraper import category_stats
        return jsonify({'ok': True, 'stats': category_stats()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/tr-yields')
def tr_yields():
    """TR 2Y ve 10Y tahvil faizleri (cache DB + scanner). ?debug=1 ile logları döner."""
    import datetime as _dt
    result = {}
    log    = []

    # ── 1. DB cache (collect_tr_yields.py tarafından doldurulur) ──
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute('SELECT ym, tr2y, tr10y FROM tr_yield_cache ORDER BY ym').fetchall()
        for ym, tr2y, tr10y in rows:
            entry = {}
            if tr2y  is not None: entry['tr2y']  = tr2y
            if tr10y is not None: entry['tr10y'] = tr10y
            if entry:
                result[ym] = entry
        log.append(f'db_cache rows={len(rows)}')
    except Exception as e:
        log.append(f'db_cache exc={e}')

    # ── 2. TradingView scanner (güncel ay — cache eksikse doldur) ─
    now_ym = _dt.datetime.utcnow().strftime('%Y-%m')
    cached = result.get(now_ym, {})
    need   = []
    if 'tr2y'  not in cached: need.append('TVC:TR02Y')
    if 'tr10y' not in cached: need.append('TVC:TR10Y')
    if need:
        try:
            r = _http.post(
                'https://scanner.tradingview.com/global/scan',
                json={'symbols': {'tickers': need, 'query': {'types': []}}, 'columns': ['close']},
                headers={
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.tradingview.com',
                    'Referer': 'https://www.tradingview.com/',
                    'User-Agent': 'Mozilla/5.0',
                },
                timeout=8
            )
            log.append(f'tv_scanner status={r.status_code} body={r.text[:200]}')
            scanner_vals = {}
            for item in r.json().get('data', []):
                s = item.get('s', '')
                c = (item.get('d') or [None])[0]
                if c is None:
                    continue
                if 'TR02Y' in s:
                    scanner_vals['tr2y']  = round(c, 2)
                elif 'TR10Y' in s:
                    scanner_vals['tr10y'] = round(c, 2)
            if scanner_vals:
                result.setdefault(now_ym, {}).update(scanner_vals)
                # cache'e de yaz
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        existing = conn.execute(
                            'SELECT tr2y, tr10y FROM tr_yield_cache WHERE ym=?', (now_ym,)
                        ).fetchone()
                        if existing:
                            new_tr2y  = scanner_vals.get('tr2y',  existing[0])
                            new_tr10y = scanner_vals.get('tr10y', existing[1])
                            conn.execute(
                                'UPDATE tr_yield_cache SET tr2y=?, tr10y=? WHERE ym=?',
                                (new_tr2y, new_tr10y, now_ym)
                            )
                        else:
                            conn.execute(
                                'INSERT INTO tr_yield_cache(ym, tr2y, tr10y) VALUES(?,?,?)',
                                (now_ym,
                                 scanner_vals.get('tr2y'),
                                 scanner_vals.get('tr10y'))
                            )
                except Exception as e:
                    log.append(f'cache_write exc={e}')
        except Exception as e:
            log.append(f'tv_scanner exc={e}')

    out = dict(result)
    if request.args.get('debug'):
        out['_debug'] = log
    return jsonify(out)


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


if __name__ == '__main__':
    app.run(debug=False, port=5000)

