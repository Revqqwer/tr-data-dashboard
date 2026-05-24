import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
HAFTALIK VERİ GÜNCELLEMESİ
DB'deki son tarihten bugüne kadar olan yeni veriyi çekip ekler.

Çalıştır: python update.py
Her hafta yeni bülten/veri yayınlandıktan sonra çalıştırılır.
"""

import sqlite3
import pandas as pd
import requests
from bs4 import BeautifulSoup
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')
import os
DB_PATH      = os.path.join(os.path.dirname(__file__), 'data', 'cache.db')
EVDS_BASE    = 'https://evds3.tcmb.gov.tr/igmevdsms-dis'
EVDS_KEY     = os.environ.get('EVDS_KEY', '')
EVDS_HEADERS = {'key': EVDS_KEY}
BDDK_URL     = 'https://www.bddk.org.tr/bultenhaftalik'
BDDK_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

TR_MONTHS = {
    'Ocak':1,'Şubat':2,'Mart':3,'Nisan':4,'Mayıs':5,'Haziran':6,
    'Temmuz':7,'Ağustos':8,'Eylül':9,'Ekim':10,'Kasım':11,'Aralık':12
}


# ══════════════════════════════════════════════
#  YARDIMCILAR
# ══════════════════════════════════════════════

def db_last(table):
    """Tablodaki en son tarihi Timestamp olarak döndür."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(f'SELECT MAX(tarih) FROM {table}').fetchone()
    return pd.Timestamp(row[0]) if row[0] else None


def build_usdtry_map():
    """DB'den interpolasyonlu günlük USDTRY dict döndür."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute('SELECT tarih, kur FROM usdtry ORDER BY tarih').fetchall()
    if not rows:
        return {}
    s = pd.Series({pd.Timestamp(r[0]): r[1] for r in rows}).sort_index()
    idx = pd.date_range(s.index.min(), s.index.max(), freq='D')
    s   = s.reindex(idx).interpolate(method='time').ffill().bfill()
    return s.to_dict()


def get_rate(usdtry_map, date):
    for delta in range(8):
        rate = usdtry_map.get(date - pd.Timedelta(days=delta))
        if rate:
            return rate
    return None


def parse_bddk_number(txt):
    return float(txt.strip().replace('.', '').replace(',', '.')) if txt.strip() else None


# ══════════════════════════════════════════════
#  USDTRY
# ══════════════════════════════════════════════

def update_usdtry():
    print('\n── USDTRY ──')
    last  = db_last('usdtry')
    today = pd.Timestamp(datetime.now().date())

    if last and (today - last).days <= 1:
        print(f'  Güncel ({last.date()}), atlandı.')
        return 0

    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y') if last else '01-01-2014'
    end_str   = today.strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.DK.USD.A'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        val = item.get('TP_DK_USD_A')
        if not val:
            continue
        d, m, y = item['Tarih'].split('-')
        rows.append((f'{y}-{m}-{d}', float(val)))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR REPLACE INTO usdtry VALUES (?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni kur kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


# ══════════════════════════════════════════════
#  DTH
# ══════════════════════════════════════════════

def update_dth():
    print('\n── DTH ──')
    last = db_last('dth')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now()
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    end_str   = today.strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.HPBITABLO4.3-TP.HPBITABLO4.8-TP.HPBITABLO4.2'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=20, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        b = item.get('TP_HPBITABLO4_3')
        if b is None:
            continue
        t   = item.get('TP_HPBITABLO4_8')
        top = item.get('TP_HPBITABLO4_2')
        d, m, y = item['Tarih'].split('-')
        rows.append((
            f'{y}-{m}-{d}',
            float(b),
            float(t)   if t   else None,
            float(top) if top else None,
        ))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO dth VALUES (?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni DTH kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


# ══════════════════════════════════════════════
#  MENKUL
# ══════════════════════════════════════════════

def update_menkul():
    print('\n── Menkul ──')
    last = db_last('menkul')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now()
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    end_str   = today.strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.MKNETHAR.M7-TP.MKNETHAR.M8'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=20, verify=False)
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

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO menkul VALUES (?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni menkul kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


# ══════════════════════════════════════════════
#  CREDIT  (BDDK scrape)
# ══════════════════════════════════════════════

def update_credit():
    print('\n── Credit (BDDK) ──')
    last = db_last('credit')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    # BDDK sayfasını çek
    r = requests.get(BDDK_URL, headers=BDDK_HEADERS, timeout=30, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')

    # Tarihi bul
    tarih = None
    for tag in soup.find_all(string=True):
        txt = tag.strip()
        for ay, no in TR_MONTHS.items():
            if ay in txt and any(str(y) in txt for y in range(2024, 2030)):
                parts = txt.split()
                try:
                    tarih = pd.Timestamp(year=int(parts[2]), month=no, day=int(parts[0]))
                    break
                except (ValueError, IndexError):
                    continue
        if tarih:
            break

    if tarih is None:
        print('  BDDK tarihi bulunamadı.')
        return 0

    if tarih <= last:
        print(f'  Güncel ({last.date()}), atlandı.')
        return 0

    # Kredi satırlarını oku
    tables = soup.find_all('table')
    if len(tables) < 3:
        print('  Tablo bulunamadı.')
        return 0

    tuketici = ticari = ticari_yp = None
    for row in tables[2].find_all('tr'):
        cells = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) < 3:
            continue
        label = cells[1].lower()
        if 'tüketici kredileri ve bireysel kredi kartlar' in label:
            tuketici  = parse_bddk_number(cells[2])
        elif 'ticari ve diğer krediler' in label:
            ticari    = parse_bddk_number(cells[2])
            ticari_yp = parse_bddk_number(cells[3]) if len(cells) > 3 else None

    if tuketici is None:
        print('  Kredi verisi okunamadı.')
        return 0

    # USDTRY'yi güncelle, kuru al
    update_usdtry()
    usdtry_map = build_usdtry_map()
    kur = get_rate(usdtry_map, tarih)

    ticari_usd = round(ticari_yp / kur, 2) if ticari_yp and kur else None
    usdtry_val = round(kur, 4) if kur else None

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT OR IGNORE INTO credit VALUES (?,?,?,?,?)',
            (tarih.strftime('%Y-%m-%d'), tuketici, ticari, ticari_usd, usdtry_val)
        )
        conn.commit()

    print(f'  +1 kayıt: {tarih.date()} | tüketici={tuketici:>15,.2f} | ticari={ticari:>15,.2f}')
    return 1


# ══════════════════════════════════════════════
#  CREDIT DETAIL  (BDDK tüketici alt kalemleri)
# ══════════════════════════════════════════════

# Tablo 3 satır numaraları → alan adları
DETAIL_ROW_MAP = {
    '4':  'konut',
    '5':  'tasit',
    '6':  'ihtiyac',
    '7':  'kk_toplam',
    '8':  'kk_taksitli',
    '9':  'kk_taksitsiz',
    '19': 'kobi',
}

def update_credit_detail(soup=None, tarih=None):
    """
    BDDK Tablo 3'ten tüketici alt kalemlerini çek ve DB'ye ekle.
    soup ve tarih parametreleri verilirse HTTP isteği atılmaz
    (update_credit ile aynı sayfayı paylaşır).
    """
    print('\n── Credit Detail (BDDK alt kalemler) ──')
    last = db_last('credit_detail')

    # Sayfa henüz çekilmemişse çek
    if soup is None:
        r = requests.get(BDDK_URL, headers=BDDK_HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')

    # Tarihi bul (verilmemişse)
    if tarih is None:
        for tag in soup.find_all(string=True):
            txt = tag.strip()
            for ay, no in TR_MONTHS.items():
                if ay in txt and any(str(y) in txt for y in range(2024, 2030)):
                    parts = txt.split()
                    try:
                        tarih = pd.Timestamp(year=int(parts[2]), month=no, day=int(parts[0]))
                        break
                    except (ValueError, IndexError):
                        continue
            if tarih:
                break

    if tarih is None:
        print('  BDDK tarihi bulunamadı.')
        return 0

    if last and tarih <= last:
        print(f'  Güncel ({last.date()}), atlandı.')
        return 0

    # Tablo 3: tam ondalıklı değerler (cells[0]=no, cells[1]=label, cells[2]=TP)
    tables = soup.find_all('table')
    if len(tables) < 4:
        print('  Tablo bulunamadı.')
        return 0

    values = {}
    for row in tables[3].find_all('tr'):
        cells = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) < 3:
            continue
        row_no = cells[0].strip()
        if row_no in DETAIL_ROW_MAP:
            values[DETAIL_ROW_MAP[row_no]] = parse_bddk_number(cells[2])

    if 'konut' not in values:
        print('  Alt kalem verileri okunamadı.')
        return 0

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            '''INSERT OR IGNORE INTO credit_detail
               VALUES (?,?,?,?,?,?,?,?)''',
            (
                tarih.strftime('%Y-%m-%d'),
                values.get('konut'),
                values.get('tasit'),
                values.get('ihtiyac'),
                values.get('kk_taksitli'),
                values.get('kk_taksitsiz'),
                values.get('kk_toplam'),
                values.get('kobi'),
            )
        )
        conn.commit()

    print(f'  +1 kayıt: {tarih.date()} | konut={values.get("konut"):>12,.2f} | ihtiyac={values.get("ihtiyac"):>12,.2f} | kobi={values.get("kobi"):>12,.2f}')
    return 1


# ══════════════════════════════════════════════
#  BÜTÇE
# ══════════════════════════════════════════════

def update_butce():
    print('\n── Bütçe ──')
    last = db_last('butce')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now()
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    end_str   = today.strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.KB.GEN01-TP.KB.GEN12-TP.KB.GEN35-TP.KB.GEN39-TP.KB.GEN17'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=20, verify=False)
    r.raise_for_status()

    usdtry_map = build_usdtry_map()
    rows = []
    for item in r.json().get('items', []):
        gelir = item.get('TP_KB_GEN01')
        gider = item.get('TP_KB_GEN12')
        denge = item.get('TP_KB_GEN35')
        nakit = item.get('TP_KB_GEN39')
        faiz  = item.get('TP_KB_GEN17')
        if gelir is None and gider is None and denge is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        kur = get_rate(usdtry_map, pd.Timestamp(tarih))
        rows.append((
            tarih,
            float(gelir) if gelir else None,
            float(gider) if gider else None,
            float(denge) if denge else None,
            round(kur, 4) if kur else None,
            float(nakit) if nakit else None,
            float(faiz)  if faiz  else None,
        ))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO butce VALUES (?,?,?,?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni bütçe kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


# ══════════════════════════════════════════════
#  DIŞ TİCARET
# ══════════════════════════════════════════════

def update_dis_ticaret():
    print('\n── Dış Ticaret ──')
    last = db_last('dis_ticaret')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now().strftime('%d-%m-%Y')
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.ODANA6.Q02-TP.ODANA6.Q03'
           f'&startDate={start_str}&endDate={today}&type=json')
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

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO dis_ticaret VALUES (?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni dış ticaret kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


# ══════════════════════════════════════════════
#  ÖZET
# ══════════════════════════════════════════════

def update_turizm():
    print('\n── Turizm ──')
    last = db_last('turizm')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now().strftime('%d-%m-%Y')
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.SGEGI.K1-TP.SGEGI.K3-TP.SGEGI.K4'
           f'&startDate={start_str}&endDate={today}&type=json')
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

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO turizm VALUES (?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni turizm kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


BOP_SERIES = ('TP.ODEAYRSUNUM6.Q1-TP.ODEAYRSUNUM6.Q4-TP.ODEAYRSUNUM6.Q20'
              '-TP.ODEAYRSUNUM6.Q68-TP.ODEAYRSUNUM6.Q92-TP.ODEAYRSUNUM6.Q99'
              '-TP.ODEAYRSUNUM6.Q210-TP.ODEAYRSUNUM6.Q101-TP.ODEAYRSUNUM6.Q204'
              '-TP.ODEAYRSUNUM6.Q136-TP.ODEAYRSUNUM6.Q114-TP.ODEAYRSUNUM6.Q102'
              '-TP.ODEAYRSUNUM6.Q115-TP.ODEAYRSUNUM6.Q119'
              '-TP.ODEAYRSUNUM6.Q103-TP.ODEAYRSUNUM6.Q108')

def update_odeme_dengesi():
    print('\n── Ödemeler Dengesi ──')
    last = db_last('odeme_dengesi')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now().strftime('%d-%m-%Y')
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    url = f'{EVDS_BASE}/series={BOP_SERIES}&startDate={start_str}&endDate={today}&type=json'
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        def f(k): v = item.get(k); return float(v) if v else None
        cari = f('TP_ODEAYRSUNUM6_Q1')
        if cari is None and f('TP_ODEAYRSUNUM6_Q101') is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        rows.append((tarih,
            f('TP_ODEAYRSUNUM6_Q1'),   f('TP_ODEAYRSUNUM6_Q4'),
            f('TP_ODEAYRSUNUM6_Q20'),  f('TP_ODEAYRSUNUM6_Q68'),
            f('TP_ODEAYRSUNUM6_Q92'),  f('TP_ODEAYRSUNUM6_Q99'),
            f('TP_ODEAYRSUNUM6_Q210'), f('TP_ODEAYRSUNUM6_Q101'),
            f('TP_ODEAYRSUNUM6_Q204'), f('TP_ODEAYRSUNUM6_Q136'),
            f('TP_ODEAYRSUNUM6_Q114'), f('TP_ODEAYRSUNUM6_Q102'),
            f('TP_ODEAYRSUNUM6_Q115'), f('TP_ODEAYRSUNUM6_Q119'),
            f('TP_ODEAYRSUNUM6_Q103'), f('TP_ODEAYRSUNUM6_Q108')))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO odeme_dengesi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni kayıt (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


KONUT_SERIES = (
    'TP.KFE.TR-TP.KFE.TR10-TP.YKFE.TR'
    '-TP.YOKFEND.TR-TP.YKKE.TR-TP.YKKE.TR10'
    '-TP.AKONUTSAT1.KTRTOPLAM-TP.AKONUTSAT2.KTRTOPLAM'
)

def update_konut():
    print('\n── Konut ──')
    last = db_last('konut')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now().strftime('%d-%m-%Y')
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series={KONUT_SERIES}'
           f'&startDate={start_str}&endDate={today}&type=json')
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
        rows.append((tarih, kfe_tr, f('TP_KFE_TR10'),
                     f('TP_YKFE_TR'), f('TP_YOKFEND_TR'),
                     f('TP_YKKE_TR'), f('TP_YKKE_TR10'),
                     f('TP_AKONUTSAT1_KTRTOPLAM'), f('TP_AKONUTSAT2_KTRTOPLAM')))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO konut VALUES (?,?,?,?,?,?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni konut kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


ENFLASYON_SERIES = (
    'TP.TUKFIY2025.GENEL-TP.TUKFIY2025.01-TP.TUKFIY2025.02-TP.TUKFIY2025.03'
    '-TP.TUKFIY2025.04-TP.TUKFIY2025.05-TP.TUKFIY2025.06-TP.TUKFIY2025.07'
    '-TP.TUKFIY2025.08-TP.TUKFIY2025.09-TP.TUKFIY2025.10-TP.TUKFIY2025.11'
    '-TP.TUKFIY2025.12-TP.TUKFIY2025.13'
)

def update_enflasyon():
    print('\n── Enflasyon (TÜFE) ──')
    last = db_last('enflasyon')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now().strftime('%d-%m-%Y')
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    url = (f'{EVDS_BASE}/series={ENFLASYON_SERIES}'
           f'&startDate={start_str}&endDate={today}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
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
        rows.append((
            tarih, genel,
            f('TP_TUKFIY2025_01'), f('TP_TUKFIY2025_02'),
            f('TP_TUKFIY2025_03'), f('TP_TUKFIY2025_04'),
            f('TP_TUKFIY2025_05'), f('TP_TUKFIY2025_06'),
            f('TP_TUKFIY2025_07'), f('TP_TUKFIY2025_08'),
            f('TP_TUKFIY2025_09'), f('TP_TUKFIY2025_10'),
            f('TP_TUKFIY2025_11'), f('TP_TUKFIY2025_12'),
            f('TP_TUKFIY2025_13'),
        ))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                'INSERT OR IGNORE INTO enflasyon VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                rows
            )
            conn.commit()
        print(f'  +{len(rows)} yeni TÜFE kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


def update_makro():
    print('\n── Makro ──')
    last = db_last('makro')
    if not last:
        print('  DB boş — önce migrate.py çalıştırın.')
        return 0

    today     = datetime.now()
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y')
    end_str   = today.strftime('%d-%m-%Y')

    # CPI Endeks
    url = (f'{EVDS_BASE}/series=TP.GENENDEKS.T1'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()
    sepet_data = {}
    for item in r.json().get('items', []):
        val = item.get('TP_GENENDEKS_T1')
        if not val: continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        sepet_data[tarih] = float(val)

    # Politika Faizi
    url = (f'{EVDS_BASE}/series=TP.BISPOLFAIZ.TUR'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()
    raw_rates = {}
    for item in r.json().get('items', []):
        val = item.get('TP_BISPOLFAIZ_TUR')
        if not val: continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        raw_rates[tarih] = float(val)

    if raw_rates:
        rs = pd.Series(raw_rates)
        rs.index = pd.to_datetime(rs.index)
        rs = rs.sort_index()
        daily_idx = pd.date_range(rs.index.min(), pd.Timestamp(today.date()), freq='D')
        daily_rs = rs.reindex(daily_idx).ffill()
        monthly_rs = daily_rs.resample('ME').last()
        pol_faiz_data = {f'{ts.year}-{ts.month:02d}-01': v for ts, v in monthly_rs.items()}
    else:
        # Forward-fill from DB if no new data
        with sqlite3.connect(DB_PATH) as conn:
            last_pf = conn.execute('SELECT pol_faiz FROM makro WHERE pol_faiz IS NOT NULL ORDER BY tarih DESC LIMIT 1').fetchone()
        pol_faiz_data = {}
        if last_pf:
            for tarih in sepet_data:
                pol_faiz_data[tarih] = last_pf[0]

    # Aylık USDTRY (DB'deki günlük tablodan hesapla)
    update_usdtry()
    with sqlite3.connect(DB_PATH) as conn:
        usdtry_rows = conn.execute(
            "SELECT strftime('%Y-%m', tarih)||'-01' AS ay, ROUND(AVG(kur), 4) FROM usdtry WHERE tarih >= ? GROUP BY ay",
            (last.strftime('%Y-%m-%d'),)
        ).fetchall()
    usdtry_data = {r[0]: r[1] for r in usdtry_rows}

    rows = [(t, sepet_data[t], usdtry_data.get(t), pol_faiz_data.get(t))
            for t in sorted(sepet_data.keys())]

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO makro VALUES (?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni kayıt (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


def update_ab_surplus():
    print('\n── TCMB Analitik Bilanço (AB) ──')
    last  = db_last('ab_surplus')
    today = datetime.now()
    start_str = (last + pd.Timedelta(days=1)).strftime('%d-%m-%Y') if last else '01-01-2010'
    end_str   = today.strftime('%d-%m-%Y')

    url = (f'{EVDS_BASE}/series=TP.AB.A02-TP.AB.A10-TP.DK.USD.A.YTL'
           f'&startDate={start_str}&endDate={end_str}&type=json')
    r = requests.get(url, headers=EVDS_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    rows = []
    for item in r.json().get('items', []):
        a02 = item.get('TP_AB_A02')
        a10 = item.get('TP_AB_A10')
        usd = item.get('TP_DK_USD_A_YTL')
        if a02 is None or a10 is None or usd is None:
            continue
        parts = item['Tarih'].split('-')
        if len(parts) == 2:
            if int(parts[0]) > 31: y, m = parts
            else:                  m, y = parts
            tarih = f'{y}-{m.zfill(2)}-01'
        else:
            d, m, y = parts; tarih = f'{y}-{m}-{d}'
        a02_v = float(a02); a10_v = float(a10); usd_v = float(usd)
        deger = round((a02_v - a10_v) / usd_v, 2) if usd_v else None
        rows.append((tarih, a02_v, a10_v, usd_v, deger))

    if rows:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany('INSERT OR IGNORE INTO ab_surplus VALUES (?,?,?,?,?)', rows)
            conn.commit()
        print(f'  +{len(rows)} yeni AB kaydı (son: {rows[-1][0]})')
    else:
        print('  Yeni veri yok.')
    return len(rows)


def print_summary():
    print('\n── DB durumu ──')
    with sqlite3.connect(DB_PATH) as conn:
        for tbl in ['dth', 'credit', 'credit_detail', 'menkul', 'usdtry', 'butce', 'dis_ticaret', 'turizm', 'odeme_dengesi', 'konut', 'enflasyon', 'makro', 'ab_surplus']:
            cnt  = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
            last = conn.execute(f'SELECT MAX(tarih) FROM {tbl}').fetchone()[0]
            print(f'  {tbl:<15} {cnt:>6} kayıt   son: {last}')


if __name__ == '__main__':
    print('=' * 50)
    print(f'  HAFTALIK GÜNCELLEME  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 50)
    update_usdtry()
    update_dth()
    update_menkul()
    update_credit()
    update_credit_detail()
    update_butce()
    update_dis_ticaret()
    update_turizm()
    update_odeme_dengesi()
    update_konut()
    update_enflasyon()
    update_makro()
    update_ab_surplus()
    print_summary()
    print('\n✓ Güncelleme tamamlandı.')
