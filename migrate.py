import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
TEK SEFERLİK VERİ MİGRASYONU
Excel + EVDS → SQLite (data/cache.db)

Çalıştır: python migrate.py
Sadece bir kez çalıştırılır. Sonrasında güncelleme için update.py kullanılır.
"""

import sqlite3
import pandas as pd
import requests
import warnings
import os
from datetime import datetime

warnings.filterwarnings('ignore')

DB_PATH      = 'data/cache.db'
EXCEL_DTH    = 'data/bop.xlsx'
EXCEL_CREDIT = 'data/bddk_credit.xlsx'
EVDS_BASE    = 'https://evds3.tcmb.gov.tr/igmevdsms-dis'
EVDS_KEY     = 'a67jeM3QJz'
EVDS_HEADERS = {'key': EVDS_KEY}


# ══════════════════════════════════════════════
#  DB KURULUMU
# ══════════════════════════════════════════════

def init_db():
    os.makedirs('data', exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS dth (
                tarih    TEXT PRIMARY KEY,
                bireysel REAL,
                tuzel    REAL,
                toplam   REAL
            );
            CREATE TABLE IF NOT EXISTS credit (
                tarih      TEXT PRIMARY KEY,
                tuketici   REAL,
                ticari     REAL,
                ticari_usd REAL,
                usdtry     REAL
            );
            CREATE TABLE IF NOT EXISTS menkul (
                tarih TEXT PRIMARY KEY,
                yil   INTEGER,
                hisse REAL,
                dibs  REAL
            );
            CREATE TABLE IF NOT EXISTS usdtry (
                tarih TEXT PRIMARY KEY,
                kur   REAL
            );
            CREATE TABLE IF NOT EXISTS credit_detail (
                tarih        TEXT PRIMARY KEY,
                konut        REAL,
                tasit        REAL,
                ihtiyac      REAL,
                kk_taksitli  REAL,
                kk_taksitsiz REAL,
                kk_toplam    REAL,
                kobi         REAL
            );
            CREATE TABLE IF NOT EXISTS butce (
                tarih        TEXT PRIMARY KEY,
                gelir        REAL,
                gider        REAL,
                denge        REAL,
                usdtry       REAL,
                nakit_denge  REAL,
                faiz         REAL
            );
            CREATE TABLE IF NOT EXISTS dis_ticaret (
                tarih   TEXT PRIMARY KEY,
                ihracat REAL,
                ithalat REAL,
                acik    REAL
            );
            CREATE TABLE IF NOT EXISTS turizm (
                tarih     TEXT PRIMARY KEY,
                gelir     REAL,
                ziyaretci REAL,
                kisi_basi REAL
            );
            CREATE TABLE IF NOT EXISTS odeme_dengesi (
                tarih      TEXT PRIMARY KEY,
                cari       REAL,
                dis_tic    REAL,
                hizmet     REAL,
                birincil   REAL,
                ikincil    REAL,
                sermaye    REAL,
                net_hata   REAL,
                finans     REAL,
                rezerv     REAL,
                diger_yat  REAL,
                portfoy    REAL,
                dogrudan   REAL
            );
            CREATE TABLE IF NOT EXISTS konut (
                tarih          TEXT PRIMARY KEY,
                kfe_tr         REAL,
                kfe_ist        REAL,
                ykfe           REAL,
                yokfe          REAL,
                ykke_tr        REAL,
                ykke_ist       REAL,
                satis_toplam   REAL,
                satis_ipotekli REAL
            );
            CREATE TABLE IF NOT EXISTS makro (
                tarih    TEXT PRIMARY KEY,
                sepet    REAL,
                usdtry   REAL,
                pol_faiz REAL
            );
            CREATE TABLE IF NOT EXISTS enflasyon (
                tarih    TEXT PRIMARY KEY,
                genel    REAL,
                gida     REAL,
                alkol    REAL,
                giyim    REAL,
                konut    REAL,
                mobilya  REAL,
                saglik   REAL,
                ulasim   REAL,
                bilgi    REAL,
                eglence  REAL,
                egitim   REAL,
                lokanta  REAL,
                sigorta  REAL,
                kisisel  REAL
            );
        ''')
        conn.commit()
    print('DB tabloları hazır.')


# ══════════════════════════════════════════════
#  USDTRY YARDIMCILARI
# ══════════════════════════════════════════════

def _build_usdtry_map():
    """DB'den interpolasyonlu günlük USDTRY dict döndür."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute('SELECT tarih, kur FROM usdtry ORDER BY tarih').fetchall()
    if not rows:
        return {}
    s = pd.Series({pd.Timestamp(r[0]): r[1] for r in rows}).sort_index()
    idx = pd.date_range(s.index.min(), s.index.max(), freq='D')
    s   = s.reindex(idx).interpolate(method='time').ffill().bfill()
    return s.to_dict()


def _get_rate(usdtry_map, date):
    for delta in range(8):
        rate = usdtry_map.get(date - pd.Timedelta(days=delta))
        if rate:
            return rate
    return None


# ══════════════════════════════════════════════
#  1. USDTRY  (günlük kur — 3 parçada EVDS)
# ══════════════════════════════════════════════

def migrate_usdtry():
    print('\n── USDTRY ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM usdtry').fetchone()[0]

    if existing > 1000:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    batches = [
        ('01-01-2014', '31-12-2017'),
        ('01-01-2018', '31-12-2021'),
        ('01-01-2022', today),
    ]
    all_rows = []
    for start, end in batches:
        url = (f'{EVDS_BASE}/series=TP.DK.USD.A'
               f'&startDate={start}&endDate={end}&type=json')
        r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        for item in r.json().get('items', []):
            val = item.get('TP_DK_USD_A')
            if not val:
                continue
            d, m, y = item['Tarih'].split('-')
            all_rows.append((f'{y}-{m}-{d}', float(val)))
        print(f'  {start} – {end}: {len(all_rows)} kayıt birikti')

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO usdtry VALUES (?,?)', all_rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM usdtry').fetchone()[0]
    print(f'  Toplam DB: {total} günlük kur')


# ══════════════════════════════════════════════
#  2. DTH  (Excel → DB)
# ══════════════════════════════════════════════

def migrate_dth():
    print('\n── DTH ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM dth').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    df = pd.read_excel(EXCEL_DTH, sheet_name='DTH', header=None)
    data = df.iloc[1:, [1, 2, 3, 6]].copy()
    data.columns = ['tarih', 'bireysel', 'tuzel', 'toplam']
    data = data.dropna(subset=['bireysel'])
    data['tarih'] = pd.to_datetime(data['tarih']).dt.strftime('%Y-%m-%d')
    data = data.sort_values('tarih').drop_duplicates('tarih')

    rows = [
        (r['tarih'],
         float(r['bireysel']) if pd.notna(r['bireysel']) else None,
         float(r['tuzel'])    if pd.notna(r['tuzel'])    else None,
         float(r['toplam'])   if pd.notna(r['toplam'])   else None)
        for _, r in data.iterrows()
    ]
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO dth VALUES (?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM dth').fetchone()[0]
    print(f'  {len(rows)} satır Excel\'den yüklendi → DB: {total}')


# ══════════════════════════════════════════════
#  3. CREDIT  (Excel → DB, usdtry dahil)
# ══════════════════════════════════════════════

def migrate_credit():
    print('\n── Credit ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM credit').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    df = pd.read_excel(EXCEL_CREDIT, sheet_name='Credit', header=0)
    data = df.iloc[:, [1, 2, 3, 5]].copy()
    data.columns = ['tarih', 'tuketici', 'ticari', 'ticari_usd']
    data = data.dropna(subset=['tarih', 'tuketici'])
    data['tarih'] = pd.to_datetime(data['tarih'])
    data = data.sort_values('tarih').drop_duplicates('tarih')

    usdtry_map = _build_usdtry_map()

    rows = []
    for _, r in data.iterrows():
        kur = _get_rate(usdtry_map, r['tarih'])
        rows.append((
            r['tarih'].strftime('%Y-%m-%d'),
            float(r['tuketici'])    if pd.notna(r['tuketici'])    else None,
            float(r['ticari'])      if pd.notna(r['ticari'])      else None,
            float(r['ticari_usd'])  if pd.notna(r['ticari_usd'])  else None,
            round(kur, 4)           if kur                         else None,
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO credit VALUES (?,?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM credit').fetchone()[0]
    print(f'  {len(rows)} satır Excel\'den yüklendi → DB: {total}')


# ══════════════════════════════════════════════
#  4. MENKUL  (EVDS → DB, tam tarih)
# ══════════════════════════════════════════════

def migrate_menkul():
    print('\n── Menkul ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM menkul').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series=TP.MKNETHAR.M7-TP.MKNETHAR.M8'
           f'&startDate=01-01-2010&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        hisse = item.get('TP_MKNETHAR_M7')
        dibs  = item.get('TP_MKNETHAR_M8')
        if hisse is None and dibs is None:
            continue
        d, m, y = item['Tarih'].split('-')
        rows.append((
            f'{y}-{m}-{d}', int(y),
            float(hisse) if hisse else None,
            float(dibs)  if dibs  else None,
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO menkul VALUES (?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM menkul').fetchone()[0]
    print(f'  {len(rows)} kayıt EVDS\'den yüklendi → DB: {total}')


# ══════════════════════════════════════════════
#  5. CREDIT DETAIL  (Tüketici alt kalemleri)
# ══════════════════════════════════════════════

EXCEL_CREDIT_DETAIL = r'C:\Users\hakan\OneDrive\Masaüstü\bundandevam.xlsx'
SHEET_CREDIT_DETAIL = 'Tüketici Kredi Alt Kalemleri'

def migrate_credit_detail():
    print('\n── Credit Detail ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM credit_detail').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    df = pd.read_excel(EXCEL_CREDIT_DETAIL, sheet_name=SHEET_CREDIT_DETAIL, header=None)

    # Veri satır 17'den başlar, sütun mapping:
    # 1=tarih, 2=konut, 11=tasit, 16=ihtiyac,
    # 21=kk_taksitli, 22=kk_taksitsiz, 25=kk_toplam, 31=kobi
    cols = [1, 2, 11, 16, 21, 22, 25, 31]
    data = df.iloc[17:, cols].copy()
    data.columns = ['tarih', 'konut', 'tasit', 'ihtiyac',
                    'kk_taksitli', 'kk_taksitsiz', 'kk_toplam', 'kobi']
    data = data.dropna(subset=['tarih'])
    data['tarih'] = pd.to_datetime(data['tarih']).dt.strftime('%Y-%m-%d')
    data = data.sort_values('tarih').drop_duplicates('tarih')

    def v(x):
        if pd.isna(x) or x == 0:
            return None
        return float(x)

    rows = [
        (r['tarih'], v(r['konut']), v(r['tasit']), v(r['ihtiyac']),
         v(r['kk_taksitli']), v(r['kk_taksitsiz']), v(r['kk_toplam']), v(r['kobi']))
        for _, r in data.iterrows()
    ]

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO credit_detail VALUES (?,?,?,?,?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM credit_detail').fetchone()[0]
    print(f'  {len(rows)} satır Excel\'den yüklendi → DB: {total}')


# ══════════════════════════════════════════════
#  5. BÜTÇE  (EVDS → DB)
# ══════════════════════════════════════════════

def migrate_butce():
    print('\n── Bütçe ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM butce').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series=TP.KB.GEN01-TP.KB.GEN12-TP.KB.GEN35-TP.KB.GEN39'
           f'&startDate=01-01-2013&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    usdtry_map = _build_usdtry_map()
    rows = []
    for item in r.json().get('items', []):
        gelir = item.get('TP_KB_GEN01')
        gider = item.get('TP_KB_GEN12')
        denge = item.get('TP_KB_GEN35')
        nakit = item.get('TP_KB_GEN39')
        if gelir is None and gider is None and denge is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        kur = _get_rate(usdtry_map, pd.Timestamp(tarih))
        rows.append((
            tarih,
            float(gelir) if gelir else None,
            float(gider) if gider else None,
            float(denge) if denge else None,
            round(kur, 4) if kur else None,
            float(nakit) if nakit else None,
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO butce VALUES (?,?,?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM butce').fetchone()[0]
    print(f'  {len(rows)} kayıt EVDS\'den yüklendi → DB: {total}')


EXCEL_BUTCE = r'C:\Users\hakan\OneDrive\Masaüstü\bundandevam.xlsx'

def migrate_butce_excel():
    """bundandevam.xlsx Bütçe EVDS sheetinden 2006-2012 tarihi verisini ekler."""
    print('\n── Bütçe Excel (2006-2012 tarihi) ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute("SELECT COUNT(*) FROM butce WHERE tarih < '2013-01-01'").fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    df = pd.read_excel(EXCEL_BUTCE, sheet_name='Bütçe EVDS', header=None)
    # Veri satır 3'ten başlar; col 1=tarih, col 2=gelir, col 12=faiz
    data = df.iloc[3:, [1, 2, 12]].copy()
    data.columns = ['tarih_raw', 'gelir', 'faiz']
    data = data.dropna(subset=['tarih_raw'])

    rows = []
    for _, r in data.iterrows():
        t = str(r['tarih_raw']).strip()
        if not t or t == 'nan':
            continue
        # "YYYY-MM" → "YYYY-MM-01"
        if len(t) == 7 and '-' in t:
            tarih = t + '-01'
        else:
            continue
        # Sadece 2013 öncesi
        if tarih >= '2013-01-01':
            continue
        gelir = float(r['gelir']) if pd.notna(r['gelir']) else None
        faiz  = float(r['faiz'])  if pd.notna(r['faiz'])  else None
        if gelir is None and faiz is None:
            continue
        rows.append((tarih, gelir, None, None, None, None, faiz))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO butce VALUES (?,?,?,?,?,?,?)', rows)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM butce WHERE tarih < '2013-01-01'").fetchone()[0]
    print(f'  {len(rows)} satır (2006-2012) eklendi → DB: {total}')


def migrate_butce_faiz():
    """Mevcut butce tablosuna faiz harcamaları kolonunu ekler ve doldurur."""
    print('\n── Bütçe Faiz Harcamaları (güncelleme) ──')
    with sqlite3.connect(DB_PATH) as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(butce)').fetchall()]
        if 'faiz' not in cols:
            conn.execute('ALTER TABLE butce ADD COLUMN faiz REAL')
            conn.commit()
            print('  faiz kolonu eklendi.')
        already = conn.execute('SELECT COUNT(*) FROM butce WHERE faiz IS NOT NULL').fetchone()[0]
    if already > 0:
        print(f'  Zaten {already} faiz kaydı var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series=TP.KB.GEN17'
           f'&startDate=01-01-2013&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    updates = []
    for item in r.json().get('items', []):
        faiz = item.get('TP_KB_GEN17')
        if faiz is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        updates.append((float(faiz), tarih))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('UPDATE butce SET faiz=? WHERE tarih=?', updates)
        conn.commit()
    print(f'  {len(updates)} satır faiz harcaması ile güncellendi.')


def migrate_butce_nakit():
    """Mevcut butce tablosuna nakit_denge kolonunu ekler ve doldurur."""
    print('\n── Bütçe Nakit Denge (güncelleme) ──')
    with sqlite3.connect(DB_PATH) as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(butce)').fetchall()]
        if 'nakit_denge' not in cols:
            conn.execute('ALTER TABLE butce ADD COLUMN nakit_denge REAL')
            conn.commit()
            print('  nakit_denge kolonu eklendi.')
        already = conn.execute('SELECT COUNT(*) FROM butce WHERE nakit_denge IS NOT NULL').fetchone()[0]
    if already > 0:
        print(f'  Zaten {already} nakit_denge kaydı var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series=TP.KB.GEN39'
           f'&startDate=01-01-2013&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    updates = []
    for item in r.json().get('items', []):
        nakit = item.get('TP_KB_GEN39')
        if nakit is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        updates.append((float(nakit), tarih))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('UPDATE butce SET nakit_denge=? WHERE tarih=?', updates)
        conn.commit()
    print(f'  {len(updates)} satır nakit_denge ile güncellendi.')


# ══════════════════════════════════════════════
#  ÖZET
# ══════════════════════════════════════════════

# ══════════════════════════════════════════════
#  8. TURİZM  (EVDS → DB)
# ══════════════════════════════════════════════

def migrate_turizm():
    print('\n── Turizm ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM turizm').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series=TP.SGEGI.K1-TP.SGEGI.K3-TP.SGEGI.K4'
           f'&startDate=01-01-2005&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        gelir     = item.get('TP_SGEGI_K1')
        ziyaretci = item.get('TP_SGEGI_K3')
        kisi_basi = item.get('TP_SGEGI_K4')
        if gelir is None and ziyaretci is None and kisi_basi is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        rows.append((
            tarih,
            float(gelir)     if gelir     else None,
            float(ziyaretci) if ziyaretci else None,
            float(kisi_basi) if kisi_basi else None,
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO turizm VALUES (?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM turizm').fetchone()[0]
    print(f'  {len(rows)} kayıt EVDS\'den yüklendi → DB: {total}')


# ══════════════════════════════════════════════
#  9. ÖDEMELER DENGESİ  (EVDS → DB)
# ══════════════════════════════════════════════

BOP_SERIES = ('TP.ODEAYRSUNUM6.Q1-TP.ODEAYRSUNUM6.Q4-TP.ODEAYRSUNUM6.Q20'
              '-TP.ODEAYRSUNUM6.Q68-TP.ODEAYRSUNUM6.Q92-TP.ODEAYRSUNUM6.Q99'
              '-TP.ODEAYRSUNUM6.Q210-TP.ODEAYRSUNUM6.Q101-TP.ODEAYRSUNUM6.Q204'
              '-TP.ODEAYRSUNUM6.Q136-TP.ODEAYRSUNUM6.Q114-TP.ODEAYRSUNUM6.Q102'
              '-TP.ODEAYRSUNUM6.Q115-TP.ODEAYRSUNUM6.Q119'
              '-TP.ODEAYRSUNUM6.Q103-TP.ODEAYRSUNUM6.Q108')

def _parse_bop_items(items):
    rows = []
    for item in items:
        def f(k): v = item.get(k); return float(v) if v else None
        cari           = f('TP_ODEAYRSUNUM6_Q1')
        dis_tic        = f('TP_ODEAYRSUNUM6_Q4')
        hizmet         = f('TP_ODEAYRSUNUM6_Q20')
        birincil       = f('TP_ODEAYRSUNUM6_Q68')
        ikincil        = f('TP_ODEAYRSUNUM6_Q92')
        sermaye        = f('TP_ODEAYRSUNUM6_Q99')
        net_hata       = f('TP_ODEAYRSUNUM6_Q210')
        finans         = f('TP_ODEAYRSUNUM6_Q101')
        rezerv         = f('TP_ODEAYRSUNUM6_Q204')
        diger          = f('TP_ODEAYRSUNUM6_Q136')
        portfoy        = f('TP_ODEAYRSUNUM6_Q114')
        dogrudan       = f('TP_ODEAYRSUNUM6_Q102')
        portfoy_varlik = f('TP_ODEAYRSUNUM6_Q115')
        portfoy_yukum  = f('TP_ODEAYRSUNUM6_Q119')
        dyd_varlik     = f('TP_ODEAYRSUNUM6_Q103')
        dyd_yukum      = f('TP_ODEAYRSUNUM6_Q108')
        if all(v is None for v in [cari, finans]):
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        rows.append((tarih, cari, dis_tic, hizmet, birincil, ikincil,
                     sermaye, net_hata, finans, rezerv, diger, portfoy, dogrudan,
                     portfoy_varlik, portfoy_yukum, dyd_varlik, dyd_yukum))
    return rows

def migrate_odeme_dengesi():
    print('\n── Ödemeler Dengesi ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM odeme_dengesi').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = f'{EVDS_BASE}/series={BOP_SERIES}&startDate=01-01-2013&endDate={today}&type=json'
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()
    rows = _parse_bop_items(r.json().get('items', []))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO odeme_dengesi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM odeme_dengesi').fetchone()[0]
    print(f'  {len(rows)} kayıt EVDS\'den yüklendi → DB: {total}')


def migrate_bop_extra():
    """4 yeni BoP alt serisini mevcut odeme_dengesi tablosuna ekler (ALTER + UPDATE)."""
    print('\n── BoP Extra (Q115/Q119/Q103/Q108) ──')
    EXTRA_COLS = [
        ('portfoy_varlik', 'TP_ODEAYRSUNUM6_Q115'),
        ('portfoy_yukum',  'TP_ODEAYRSUNUM6_Q119'),
        ('dyd_varlik',     'TP_ODEAYRSUNUM6_Q103'),
        ('dyd_yukum',      'TP_ODEAYRSUNUM6_Q108'),
    ]
    EXTRA_SERIES = ('TP.ODEAYRSUNUM6.Q115-TP.ODEAYRSUNUM6.Q119'
                    '-TP.ODEAYRSUNUM6.Q103-TP.ODEAYRSUNUM6.Q108')

    with sqlite3.connect(DB_PATH) as conn:
        existing_cols = [r[1] for r in conn.execute('PRAGMA table_info(odeme_dengesi)').fetchall()]
        for col, _ in EXTRA_COLS:
            if col not in existing_cols:
                conn.execute(f'ALTER TABLE odeme_dengesi ADD COLUMN {col} REAL')
                print(f'  Kolon eklendi: {col}')
        conn.commit()

        # Zaten dolu mu?
        already = conn.execute('SELECT COUNT(*) FROM odeme_dengesi WHERE portfoy_varlik IS NOT NULL').fetchone()[0]
    if already > 0:
        print(f'  Zaten {already} kayıt dolu, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = f'{EVDS_BASE}/series={EXTRA_SERIES}&startDate=01-01-2013&endDate={today}&type=json'
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    updates = []
    for item in r.json().get('items', []):
        def f(k): v = item.get(k); return float(v) if v else None
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        updates.append((
            f('TP_ODEAYRSUNUM6_Q115'), f('TP_ODEAYRSUNUM6_Q119'),
            f('TP_ODEAYRSUNUM6_Q103'), f('TP_ODEAYRSUNUM6_Q108'),
            tarih,
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            '''UPDATE odeme_dengesi
               SET portfoy_varlik=?, portfoy_yukum=?, dyd_varlik=?, dyd_yukum=?
               WHERE tarih=?''',
            updates
        )
        conn.commit()
    print(f'  {len(updates)} satır güncellendi.')


KONUT_SERIES = (
    'TP.KFE.TR-TP.KFE.TR10-TP.YKFE.TR'
    '-TP.YOKFEND.TR-TP.YKKE.TR-TP.YKKE.TR10'
)

def migrate_konut():
    print('\n── Konut ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM konut').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series={KONUT_SERIES}'
           f'&startDate=01-01-2010&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        def f(k): v = item.get(k); return float(v) if v else None
        kfe_tr = f('TP_KFE_TR')
        if kfe_tr is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        rows.append((
            tarih,
            kfe_tr,
            f('TP_KFE_TR10'),
            f('TP_YKFE_TR'),
            f('TP_YOKFEND_TR'),
            f('TP_YKKE_TR'),
            f('TP_YKKE_TR10'),
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO konut VALUES (?,?,?,?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM konut').fetchone()[0]
    print(f'  {len(rows)} kayıt EVDS\'den yüklendi → DB: {total}')


KONUT_SATIS_SERIES = 'TP.AKONUTSAT1.KTRTOPLAM-TP.AKONUTSAT2.KTRTOPLAM'

def migrate_konut_satis():
    """Konut tablosuna satış verilerini ekler (ALTER + backfill)."""
    print('\n── Konut Satışları ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing_cols = [r[1] for r in conn.execute('PRAGMA table_info(konut)').fetchall()]
        for col in ['satis_toplam', 'satis_ipotekli']:
            if col not in existing_cols:
                conn.execute(f'ALTER TABLE konut ADD COLUMN {col} REAL')
                print(f'  Kolon eklendi: {col}')
        conn.commit()
        already = conn.execute('SELECT COUNT(*) FROM konut WHERE satis_toplam IS NOT NULL').fetchone()[0]
    if already > 0:
        print(f'  Zaten {already} kayıt dolu, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series={KONUT_SATIS_SERIES}'
           f'&startDate=01-01-2010&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    updates = []
    for item in r.json().get('items', []):
        toplam   = item.get('TP_AKONUTSAT1_KTRTOPLAM')
        ipotekli = item.get('TP_AKONUTSAT2_KTRTOPLAM')
        if toplam is None and ipotekli is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        updates.append((
            float(toplam)   if toplam   else None,
            float(ipotekli) if ipotekli else None,
            tarih,
        ))

    with sqlite3.connect(DB_PATH) as conn:
        # Tabloda olmayan tarihleri ekle (fiyat endeksi yoksa bile)
        conn.executemany(
            'INSERT OR IGNORE INTO konut (tarih) VALUES (?)',
            [(t,) for _, _, t in updates]
        )
        conn.executemany(
            'UPDATE konut SET satis_toplam=?, satis_ipotekli=? WHERE tarih=?',
            updates
        )
        conn.commit()
    print(f'  {len(updates)} satır satış verisiyle güncellendi.')


ENFLASYON_SERIES = (
    'TP.TUKFIY2025.GENEL-TP.TUKFIY2025.01-TP.TUKFIY2025.02-TP.TUKFIY2025.03'
    '-TP.TUKFIY2025.04-TP.TUKFIY2025.05-TP.TUKFIY2025.06-TP.TUKFIY2025.07'
    '-TP.TUKFIY2025.08-TP.TUKFIY2025.09-TP.TUKFIY2025.10-TP.TUKFIY2025.11'
    '-TP.TUKFIY2025.12-TP.TUKFIY2025.13'
)

ENFLASYON_KEYS = [
    ('genel',   'TP_TUKFIY2025_GENEL'),
    ('gida',    'TP_TUKFIY2025_01'),
    ('alkol',   'TP_TUKFIY2025_02'),
    ('giyim',   'TP_TUKFIY2025_03'),
    ('konut',   'TP_TUKFIY2025_04'),
    ('mobilya', 'TP_TUKFIY2025_05'),
    ('saglik',  'TP_TUKFIY2025_06'),
    ('ulasim',  'TP_TUKFIY2025_07'),
    ('bilgi',   'TP_TUKFIY2025_08'),
    ('eglence', 'TP_TUKFIY2025_09'),
    ('egitim',  'TP_TUKFIY2025_10'),
    ('lokanta', 'TP_TUKFIY2025_11'),
    ('sigorta', 'TP_TUKFIY2025_12'),
    ('kisisel', 'TP_TUKFIY2025_13'),
]

def migrate_enflasyon():
    print('\n── Enflasyon (TÜFE) ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM enflasyon').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    # 3 parçada çek (EVDS limit)
    batches = [
        ('01-01-2003', '31-12-2012'),
        ('01-01-2013', '31-12-2020'),
        ('01-01-2021', today),
    ]
    all_rows = []
    for start, end in batches:
        url = (f'{EVDS_BASE}/series={ENFLASYON_SERIES}'
               f'&startDate={start}&endDate={end}&type=json')
        r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        for item in r.json().get('items', []):
            def f(k): v = item.get(k); return float(v) if v else None
            genel = f('TP_TUKFIY2025_GENEL')
            if genel is None:
                continue
            parts = item['Tarih'].split('-')
            if len(parts) == 2:
                if int(parts[0]) > 31: y, m = parts
                else:                  m, y = parts
                tarih = f'{y}-{m.zfill(2)}-01'
            else:
                d, m, y = parts; tarih = f'{y}-{m}-{d}'
            all_rows.append((
                tarih,
                genel,
                f('TP_TUKFIY2025_01'), f('TP_TUKFIY2025_02'),
                f('TP_TUKFIY2025_03'), f('TP_TUKFIY2025_04'),
                f('TP_TUKFIY2025_05'), f('TP_TUKFIY2025_06'),
                f('TP_TUKFIY2025_07'), f('TP_TUKFIY2025_08'),
                f('TP_TUKFIY2025_09'), f('TP_TUKFIY2025_10'),
                f('TP_TUKFIY2025_11'), f('TP_TUKFIY2025_12'),
                f('TP_TUKFIY2025_13'),
            ))
        print(f'  {start}–{end}: {len(all_rows)} kayıt birikti')

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            'INSERT OR IGNORE INTO enflasyon VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            all_rows
        )
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM enflasyon').fetchone()[0]
    print(f'  Toplam DB: {total} aylık TÜFE kaydı')


def migrate_makro():
    print('\n── Makro (CPI Endeks + Kur + Politika Faizi) ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM makro').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')

    # ── 1. CPI Genel Endeks (TP.GENENDEKS.T1) ──
    print('  CPI Endeks çekiliyor...')
    sepet_data = {}
    for start, end in [('01-01-2003', '31-12-2015'), ('01-01-2016', today)]:
        url = (f'{EVDS_BASE}/series=TP.GENENDEKS.T1'
               f'&startDate={start}&endDate={end}&type=json')
        r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        for item in r.json().get('items', []):
            val = item.get('TP_GENENDEKS_T1')
            if not val:
                continue
            parts = item['Tarih'].split('-')
            if len(parts) == 2:
                if int(parts[0]) > 31: y, m = parts
                else:                  m, y = parts
                tarih = f'{y}-{m.zfill(2)}-01'
            else:
                d, m, y = parts; tarih = f'{y}-{m}-{d}'
            sepet_data[tarih] = float(val)
        print(f'    {start}–{end}: toplam {len(sepet_data)} CPI kaydı')

    # ── 2. TCMB Politika Faizi (TP.BISPOLFAIZ.TUR) ──
    print('  Politika Faizi çekiliyor...')
    url = (f'{EVDS_BASE}/series=TP.BISPOLFAIZ.TUR'
           f'&startDate=01-01-2003&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    raw_rates = {}
    for item in r.json().get('items', []):
        val = item.get('TP_BISPOLFAIZ_TUR')
        if not val:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        raw_rates[tarih] = float(val)

    # Forward-fill to monthly via pandas
    if raw_rates:
        rs = pd.Series(raw_rates)
        rs.index = pd.to_datetime(rs.index)
        rs = rs.sort_index()
        daily_idx = pd.date_range(rs.index.min(), pd.Timestamp(datetime.now().date()), freq='D')
        daily_rs = rs.reindex(daily_idx).ffill()
        monthly_rs = daily_rs.resample('ME').last()
        pol_faiz_data = {f'{ts.year}-{ts.month:02d}-01': v for ts, v in monthly_rs.items()}
    else:
        pol_faiz_data = {}
    print(f'    {len(pol_faiz_data)} aylık politika faizi kaydı')

    # ── 3. Aylık USDTRY (mevcut günlük tablodan avg) ──
    with sqlite3.connect(DB_PATH) as conn:
        usdtry_rows = conn.execute(
            "SELECT strftime('%Y-%m', tarih)||'-01' AS ay, ROUND(AVG(kur), 4) FROM usdtry GROUP BY ay"
        ).fetchall()
    usdtry_data = {r[0]: r[1] for r in usdtry_rows}
    print(f'    {len(usdtry_data)} aylık kur kaydı')

    # ── 4. Birleştir ve DB'ye yaz ──
    rows = []
    for tarih in sorted(sepet_data.keys()):
        rows.append((
            tarih,
            sepet_data[tarih],
            usdtry_data.get(tarih),
            pol_faiz_data.get(tarih),
        ))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO makro VALUES (?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM makro').fetchone()[0]
    print(f'  {len(rows)} kayıt → DB: {total}')


def print_summary():
    print('\n── Özet ──')
    with sqlite3.connect(DB_PATH) as conn:
        for tbl in ['dth', 'credit', 'credit_detail', 'menkul', 'usdtry', 'butce', 'dis_ticaret', 'turizm', 'odeme_dengesi', 'konut', 'enflasyon', 'makro']:
            cnt  = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
            last = conn.execute(f'SELECT MAX(tarih) FROM {tbl}').fetchone()[0]
            print(f'  {tbl:<15} {cnt:>6} kayıt  son: {last}')


# ══════════════════════════════════════════════
#  7. DIŞ TİCARET  (EVDS → DB)
# ══════════════════════════════════════════════

def migrate_dis_ticaret():
    print('\n── Dış Ticaret ──')
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute('SELECT COUNT(*) FROM dis_ticaret').fetchone()[0]
    if existing > 0:
        print(f'  Zaten {existing} kayıt var, atlandı.')
        return

    today = datetime.now().strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series=TP.ODANA6.Q02-TP.ODANA6.Q03'
           f'&startDate=01-01-2013&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        ih = item.get('TP_ODANA6_Q02')
        it = item.get('TP_ODANA6_Q03')
        if ih is None and it is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        ih_v = float(ih) if ih else None
        it_v = float(it) if it else None
        acik = round(ih_v - it_v, 2) if (ih_v is not None and it_v is not None) else None
        rows.append((tarih, ih_v, it_v, acik))

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany('INSERT OR IGNORE INTO dis_ticaret VALUES (?,?,?,?)', rows)
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM dis_ticaret').fetchone()[0]
    print(f'  {len(rows)} kayıt EVDS\'den yüklendi → DB: {total}')


if __name__ == '__main__':
    print('=' * 50)
    print('  VERİ MİGRASYONU')
    print('=' * 50)
    init_db()
    migrate_usdtry()
    migrate_dth()
    migrate_credit()
    migrate_credit_detail()
    migrate_menkul()
    migrate_butce()
    migrate_butce_faiz()
    migrate_butce_nakit()
    migrate_butce_excel()
    migrate_dis_ticaret()
    migrate_turizm()
    migrate_odeme_dengesi()
    migrate_bop_extra()
    migrate_konut()
    migrate_konut_satis()
    migrate_enflasyon()
    migrate_makro()
    print_summary()
    print('\n✓ Migrasyon tamamlandı.')
