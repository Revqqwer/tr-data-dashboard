# -*- coding: utf-8 -*-
"""
KAP fon "Portföy Dağılım Raporu" toplayıcı + ayrıştırıcı (KAP'ın kamuya açık uç noktaları).

Akış (fon başına):
  1) fon listesi    : GET /tr/YatirimFonlari/<TİP>  (tablo satırları: kod, permalink, ad)
  2) fon kimlikleri : GET /tr/fon-bildirimleri/<permalink>  -> mkkMemberOid (fon kimliği) +
                      bildirim konuları içinden "Portföy Dağılım Raporu" konu kimliği
  3) rapor listesi  : GET /tr/api/disclosure/filter/FILTERYFBF/<fon>/<konu>/<gün>
  4) ek + PDF       : GET /tr/api/notification/attachment-detail/<index>  ->  /tr/api/file/download/<objId>
  5) ayrıştırma     : "A.PAY" bölümü satırları -> hisse bazında lot, değer, ağırlık, ortalama maliyet

Emeklilik fonları (EYF) KAP'ta bu raporu yayımlamaz; yalnızca YF, BYF, YYF taranır.
Para piyasası / likit / borçlanma araçları fonları hisse taşımadığı için atlanır.

Veri data/kap_fund_holdings.db'ye yazılır (gitignore). İstekler kısıtlı hızda yapılır.

CLI:
    python kap_funds.py discover                 # fon listesi + kimlikler (önbellekli, devam eder)
    python kap_funds.py run [--days 130] [--budget 60] [--workers 2]
    python kap_funds.py monthly [--budget 240]   # PA günlük görevi: eksik fonları tamamlar (bitmişse istek atmaz)
    python kap_funds.py fund MAC                 # tek fon
    python kap_funds.py status
"""
import argparse
import html
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

BASE = 'https://www.kap.org.tr/tr'
HEADERS = {'User-Agent': 'Mozilla/5.0 (3nfinans fon takip)', 'Accept-Language': 'tr'}
FUND_TYPES = ('YF', 'BYF', 'YYF')
SKIP_NAME = re.compile(r'PARA P[İI]YASASI|L[İI]K[İI]T|BORÇLANMA ARAÇLARI|KISA VADEL[İI] BORÇ', re.I)
RATE_SLEEP = 1.2            # her istekten sonra (worker başına)

_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_ROOT, 'data', 'kap_fund_holdings.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS kap_funds (
    code TEXT PRIMARY KEY, name TEXT, type TEXT, permalink TEXT,
    oid TEXT, subject_oid TEXT,
    status TEXT,                       -- NULL: çözülmedi | ok | no_report | skipped | error
    last_checked TEXT
);
CREATE TABLE IF NOT EXISTS kap_reports (
    fund_code TEXT NOT NULL, period TEXT NOT NULL,            -- period: YYYY-MM (rapor ayı)
    disclosure_index INTEGER, published TEXT, fund_value REAL, stock_pct REAL,
    PRIMARY KEY (fund_code, period)
);
CREATE TABLE IF NOT EXISTS kap_holdings (
    fund_code TEXT NOT NULL, period TEXT NOT NULL, stock TEXT NOT NULL,
    isin TEXT, name TEXT, lots REAL, value REAL, weight_pct REAL, avg_cost REAL,
    PRIMARY KEY (fund_code, period, stock)
);
CREATE INDEX IF NOT EXISTS idx_kh_stock ON kap_holdings (stock, period);
CREATE INDEX IF NOT EXISTS idx_kh_period ON kap_holdings (period);
"""

_rate_lock = threading.Lock()
PARSER_VERSION = 4            # ayrıştırıcı iyileşince artır: eski sürümle ayrıştırılan boş raporlar yeniden denenir

_CLASS_RULES = (              # (sınıf, ad içinde aranacak kalıplar) — ilk eşleşen kazanır
    ('BYF', ('BORSA YATIRIM FONU',)),
    ('Fon Sepeti', ('FON SEPETİ', 'FON SEPETI')),
    ('Serbest', ('SERBEST',)),
    ('Hisse Senedi Yoğun', ('HİSSE SENEDİ', 'HISSE SENEDI')),
    ('Değişken', ('DEĞİŞKEN', 'DEGISKEN')),
    ('Karma', ('KARMA',)),
    ('Endeks', ('ENDEKS',)),
    ('Altın / Kıymetli Maden', ('ALTIN', 'KIYMETLİ MADEN', 'GÜMÜŞ', 'PLATİN')),
    ('Borçlanma Araçları', ('BORÇLANMA ARAÇLARI', 'BORCLANMA ARACLARI')),
    ('Para Piyasası / Likit', ('PARA PİYASASI', 'PARA PIYASASI', 'LİKİT', 'LIKIT')),
    ('Katılım', ('KATILIM',)),
    ('Yabancı', ('YABANCI', 'FUNDS', ' SICAV')),
)


def classify(name: str) -> str:
    """Fon adından klasman (KAP fon adında sınıf ifadesi geçer: '(HİSSE SENEDİ YOĞUN FON)', 'SERBEST FON' vb.)."""
    u = (name or '').upper()
    for cls, keys in _CLASS_RULES:
        if any(k in u for k in keys):
            return cls
    return 'Diğer'


# Hisse taşıması beklenmeyen sınıflar: yeniden ayrıştırmada atlanır
_NO_STOCK_CLASSES = ('Fon Sepeti', 'Borçlanma Araçları', 'Para Piyasası / Likit', 'Altın / Kıymetli Maden')


def _get(path: str, raw=False, tries=5):
    url = path if path.startswith('http') else f'{BASE}/{path}'
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
                data = r.read()
            time.sleep(RATE_SLEEP)
            return data if raw else json.loads(data.decode('utf-8'))
        except urllib.error.HTTPError as e:
            last = e
            # 429 (istek sınırı): uzun bekle ve tekrar dene
            time.sleep(45 * (i + 1) if e.code == 429 else 2 * (i + 1))
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f'{url}: {last}')


# ── Fon keşfi ───────────────────────────────────────────────────────────────
def list_funds(fund_type: str) -> list:
    raw = _get(f'https://www.kap.org.tr/tr/YatirimFonlari/{fund_type}', raw=True).decode('utf-8', 'ignore')
    s = html.unescape(raw)
    out = {}
    for m in re.finditer(r'<a href="/tr/fon-bilgileri/ozet/([^"]+)"><span[^>]*>([A-Z0-9]+)</span></a></td>'
                         r'<td[^>]*><a href="[^"]+">([^<]*)</a>', s):
        out[m.group(2)] = {'code': m.group(2), 'permalink': m.group(1), 'name': m.group(3).strip(), 'type': fund_type}
    return list(out.values())


def resolve_fund(permalink: str):
    """(fon kimliği, 'Portföy Dağılım Raporu' konu kimliği) — konu yoksa ikincisi None."""
    raw = _get(f'https://www.kap.org.tr/tr/fon-bildirimleri/{permalink}', raw=True).decode('utf-8', 'ignore')
    s = html.unescape(raw).replace('\\"', '"')
    oid = (re.findall(r'"mkkMemberOid":"([0-9A-Fa-f]{32})"', s) or [None])[0]
    subj = None
    for val, label in re.findall(r'\{"value":"([0-9a-f]{32})","label":"([^"]+)"\}', s):
        if label.strip() == 'Portföy Dağılım Raporu':
            subj = val
            break
    return oid, subj


def fund_reports(oid: str, subject: str, days: int) -> list:
    items = _get(f'api/disclosure/filter/FILTERYFBF/{oid}/{subject}/{min(days, 365)}')   # KAP 365 günden fazlasında boş liste döner
    res = []
    for it in items:
        b = it['disclosureBasic']
        # Yalnızca aylık raporlar (haftalık raporların 'dönem' numarası 13+ olur)
        if b.get('title') != 'Portföy Dağılım Raporu' or not b.get('year') or not b.get('donem') or int(b['donem']) > 12:
            continue
        res.append({'index': b['disclosureIndex'], 'published': b['publishDate'],
                    'period': f"{b['year']}-{int(b['donem']):02d}"})
    return res


class NoPdf(RuntimeError):
    """Duyuruda PDF eki yok: fon (nitelikli yatırımcı fonu vb.) portföy dağılım raporu yükümlülüğünden muaf; okunacak veri yok."""


def _pdf_ids(disclosure_index: int) -> list:
    det = _get(f'api/notification/attachment-detail/{disclosure_index}')
    ids = []

    def walk(o):
        if isinstance(o, dict):
            if 'objId' in o and str(o.get('fileExtension', 'pdf')).lower() == 'pdf':
                ids.append(o['objId'])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(det)
    if not ids:
        raise NoPdf(f'{disclosure_index}: PDF eki yok')
    return list(dict.fromkeys(ids))


def report_pdf(disclosure_index: int) -> bytes:
    return _get(f'api/file/download/{_pdf_ids(disclosure_index)[0]}', raw=True)


# ── PDF ayrıştırma ──────────────────────────────────────────────────────────
def _num(s: str) -> float:
    return float(s.replace('.', '').replace(',', '.').replace('%', ''))      # Türkçe (eski ad)


_pdf_lock = threading.Lock()      # PDFium iş parçacığı güvenli DEĞİL (çoklu iş parçacığında çöker)
_heavy_lock = threading.Lock()    # pdfplumber / OCR: bellek yiyen okumalar aynı anda tek tane

MEM_LIMIT_MB = int(os.environ.get('KAP_MEM_LIMIT_MB', '1400'))   # PA hesap sınırı 3 GB; süreç bunu aşmadan kendini bitirir


def _rss_mb() -> float:
    """Sürecin bellek kullanımı (MB); /proc yoksa (Windows) 0."""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def _mem_ok(log=None) -> bool:
    """Bellek sınırı aşıldıysa False: yeni iş başlatılmaz, süreç temiz kapanır (saatlik görev taze süreçle devam eder)."""
    m = _rss_mb()
    if m > MEM_LIMIT_MB:
        if log:
            log(f'bellek {m:.0f} MB > {MEM_LIMIT_MB} MB: yeni iş başlatılmıyor, süreç kapanıyor (sonraki çalıştırma devam eder)')
        return False
    return True


def _pdf_lines(pdf_bytes: bytes) -> list:
    try:
        import pypdfium2 as pdfium
        with _pdf_lock:
            pdf = pdfium.PdfDocument(pdf_bytes)
            lines = []
            for i in range(len(pdf)):
                page = pdf[i]
                tp = page.get_textpage()
                lines += tp.get_text_range().replace('\r', '').split('\n')
                tp.close()
                page.close()
            pdf.close()
        return lines
    except ImportError:
        import pdfplumber
        lines = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as p:
            for pg in p.pages:
                lines += (pg.extract_text() or '').split('\n')
        return lines


_DATE = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')
_NUM = r'(\d[\d.]*,\d+)'
# Şablon B satırı:  TL [ISIN] nominal maliyet gg/aa/yy seansNo fiyat değer grup% ara% toplam%
_ROW_B = re.compile(r'^TL\s+(?:(TR[A-Z0-9]{9}\d)\s+)?' + _NUM + r'\s+' + _NUM + r'\s+(\d{2}/\d{2}/\d{2,4})\s+\S+\s+'
                    + _NUM + r'\s+' + _NUM + r'\s+' + _NUM + r'\s+' + _NUM + r'\s+' + _NUM + r'$')
# Şablon C (Ziraat): 'sıra KOD.E ŞİRKET nominal değer oran% fiyat'   (sayılar Türkçe: 1.400.001,000)
_ROW_C = re.compile(r'^\s*\d+\s+([A-Z0-9]{3,6})\.E\s+(.*?)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s*$')
# Şablon D (Yapı Kredi): 'KOD ISIN ŞİRKET nominal değer oran'   (sayılar İngilizce: 600,200.00)
_ROW_D = re.compile(r'^([A-Z0-9]{3,6})\s+(TR[A-Z0-9]{9}\d)\s+(.+?)\s+(\d[\d,]*\.\d+)\s+(\d[\d,]*\.\d+)\s+(\d[\d,]*\.\d+)\s*$')
_ISIN = re.compile(r'^TR[A-Z0-9]{9}\d$')
# Şablon H (İş/AK Portföy vb.): 'KOD TL [ŞİRKET] ISIN nominal maliyet gg/aa/yy fiyat değer grup% ara% toplam%'
_N = r'(\d[\d.,]*)'
_ROW_H = re.compile(r'^([A-Z0-9]{3,6})\s+TL\s+(?:(.*?)\s+)?(TR[A-Z0-9]{9}\d)\s+' + _N + r'\s+' + _N + r'\s+(\d{2}/\d{2}/\d{2,4})\s+(?:\d{8,}\s+)?'
                    + _N + r'\s+' + _N + r'\s+' + _N + r'\s+' + _N + r'\s+' + _N + r'$')
_tickers_cache = None


def _num_tr(s: str) -> float:
    return float(s.replace('.', '').replace(',', '.').replace('%', ''))


def _num_en(s: str) -> float:
    return float(s.replace(',', '').replace('%', ''))


def _num_auto(s: str) -> float:
    """Biçimi kendisi çözer: hem ',' hem '.' varsa sonuncusu ondalıktır."""
    s = s.strip().replace('%', '')
    if ',' in s and '.' in s:
        return _num_en(s) if s.rfind('.') > s.rfind(',') else _num_tr(s)
    if ',' in s:
        return _num_en(s) if re.fullmatch(r'\d{1,3}(,\d{3})+', s) else _num_tr(s)
    if '.' in s:
        return _num_tr(s) if re.fullmatch(r'\d{1,3}(\.\d{3})+', s) else float(s)
    return float(s)


def _doc_num(lines):
    """Belgenin sayı biçimi: İngilizce (1,234.56) ağırlıklıysa _num_en, değilse _num_tr."""
    en = tr = 0
    for l in lines:
        en += bool(re.search(r'\d{1,3}(,\d{3})+\.\d{2}\b', l))
        tr += bool(re.search(r'\d{1,3}(\.\d{3})+,\d{2}\b', l))
    return _num_en if en > tr else _num_tr


def _tickers() -> set:
    """Hisse kodu doğrulama kümesi: BofA/AKD verisindeki kodlar + daha önce ayrıştırılanlar."""
    global _tickers_cache
    if _tickers_cache is None:
        t = set()
        for path, q in ((os.path.join(_ROOT, 'data', 'bofa_akd.db'), "SELECT DISTINCT code FROM bofa_daily WHERE code NOT LIKE '%.F'"),
                        (DB_PATH, 'SELECT DISTINCT stock FROM kap_holdings')):
            try:
                c = sqlite3.connect(path)
                t |= {r[0] for r in c.execute(q)}
                c.close()
            except Exception:                        # noqa: BLE001
                pass
        _tickers_cache = t
    return _tickers_cache


def _add(agg, code, isin, name, nominal, cost, value, pct):
    a = agg.setdefault(code, {'stock': code, 'isin': isin, 'name': name, 'lots': 0.0, 'value': 0.0,
                              'weight_pct': 0.0, 'cost_x_lot': 0.0, 'cost_lots': 0.0})
    if isin and not a['isin']:
        a['isin'] = isin
    if len(name or '') > len(a['name'] or ''):
        a['name'] = name
    a['lots'] += nominal
    a['value'] += value
    a['weight_pct'] += pct
    if cost is not None:                       # bazı şablonlarda alış maliyeti yok
        a['cost_x_lot'] += cost * nominal
        a['cost_lots'] += nominal


def _parse_a(lines, num) -> dict:
    """Şablon A: 'KOD.E  ŞİRKET ADI  ISIN ... nominal maliyet gg.aa.yyyy ... değer grup% toplam%'"""
    agg = {}
    for ln in lines:
        t = ln.split()
        if len(t) < 12 or not re.match(r'^[A-Z0-9]+\.[A-Z]$', t[0]):
            continue
        if not (t[-1].endswith('%') and t[-2].endswith('%')):
            continue
        isin = next((x for x in t if _ISIN.match(x)), None)
        di = next((i for i, x in enumerate(t) if _DATE.match(x)), None)
        if not isin or di is None or di < 3:
            continue
        try:
            nominal, cost = num(t[di - 2]), num(t[di - 1])
            value, pct = num(t[-3]), num(t[-1])
        except ValueError:
            continue
        _add(agg, t[0].split('.')[0], isin, ' '.join(t[1:t.index(isin)]), nominal, cost, value, pct)
    return agg


def _parse_b(lines, num) -> dict:
    """Şablon B: 'KOD ŞİRKET' satırı + ad parçaları + 'TL [ISIN] nominal maliyet gg/aa/yy ... değer % % %' satırı."""
    tick = _tickers()
    agg, cur, cur_name = {}, None, ''
    for i, ln in enumerate(lines):
        t = ln.split()
        if t and t[0] in tick and (len(t) >= 2) and not re.match(r'^\d', t[0]):
            cur, cur_name = t[0], ' '.join(t[1:])
            continue
        m = _ROW_B.match(ln.strip())
        if not m or not cur:
            continue
        isin, nominal, cost, _d, _price, value, _g, _mid, tot = m.groups()
        if not isin:                                   # ISIN bir alt/üst satırda olabilir
            for j in (i + 1, i - 1):
                if 0 <= j < len(lines) and _ISIN.match(lines[j].strip()):
                    isin = lines[j].strip()
                    break
        try:
            _add(agg, cur, isin, cur_name, num(nominal), num(cost), num(value), num(tot))
        except ValueError:
            continue
    return agg


def _parse_c(lines, num) -> dict:
    """Şablon C (Ziraat): 'sıra KOD.E ŞİRKET nominal değer oran% fiyat' — maliyet yok; '.F' (fon) satırları atlanır."""
    agg = {}
    for ln in lines:
        m = _ROW_C.match(ln)
        if not m:
            continue
        code, name, nominal, value, pct, _price = m.groups()
        try:
            _add(agg, code, None, name, _num_tr(nominal), None, _num_tr(value), _num_tr(pct))
        except ValueError:
            continue
    return agg


def _parse_d(lines, num) -> dict:
    """Şablon D (Yapı Kredi): 'KOD ISIN ŞİRKET nominal değer oran' — İngilizce sayılar; yalnızca 'A) HİSSE SENETLERİ' bölümü."""
    agg, inside = {}, False
    for ln in lines:
        u = ln.strip().upper().replace('İ', 'I')
        if re.match(r'^A\)\s*HISSE SENETLERI', u):
            inside = True
            continue
        if inside and re.match(r'^[B-Z]\)\s*\S', u):
            inside = False
        if not inside:
            continue
        m = _ROW_D.match(ln.strip())
        if not m:
            continue
        code, isin, name, nominal, value, pct = m.groups()
        try:
            _add(agg, code, isin, name, _num_en(nominal), None, _num_en(value), _num_en(pct))
        except ValueError:
            continue
    return agg


def _fund_value(lines, num):
    for ln in lines:
        m = re.search(r'TOPLAM DE.ER:\s*\(TL\)\s*([\d.,]+)', ln)
        if m:
            try:
                return _num_auto(m.group(1))
            except ValueError:
                return None
    return None


# Şablon E (Aktif): tek satırda 'KOD ... ISIN nominal maliyet gg/aa/yy seans fiyat değer % % %'
_ROW_E = re.compile(r'^([A-Z0-9]{3,6})\s+.*?(TR[A-Z0-9]{9}\d)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s+\d{2}/\d{2}/\d{2,4}\s+\S+\s+'
                    r'(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)\s+(\d[\d.]*,\d+)$')
# Şablon F (Astra/Ata/Atlas/...): 'KOD ŞİRKET nominal değer oran%'  (oran % ile başlayabilir/bitebilir; sayılar TR ya da EN)
_ROW_F = re.compile(r'^([A-Z0-9]{3,6})\s+(.+?)\s+(\d[\d.,]*\d)\s+(\d[\d.,]*\d)\s+%?\s?(\d[\d.,]*\d)\s?%?$')


def _parse_e(lines, num) -> dict:
    agg = {}
    tick = _tickers()
    for ln in lines:
        m = _ROW_E.match(ln.strip())
        if not m or (tick and m.group(1) not in tick):
            continue
        code, isin, nominal, cost, _price, value, _g, _mid, tot = m.groups()
        try:
            _add(agg, code, isin, code, _num_tr(nominal), _num_tr(cost), _num_tr(value), _num_tr(tot))
        except ValueError:
            continue
    return agg


def _parse_f(lines, num) -> dict:
    """Hisse kodu (bilinen kodlar) + ad + nominal + değer + oran. Ondalık biçimi belgeye göre (TR/EN)."""
    agg = {}
    tick = _tickers()
    if not tick:
        return agg
    for ln in lines:
        s0 = ln.strip()
        if '%' not in s0:
            continue
        m = _ROW_F.match(s0)
        if not m or m.group(1) not in tick:
            continue
        code, name, nominal, value, pct = m.groups()
        try:
            _add(agg, code, None, name, num(nominal), None, num(value), num(pct))
        except ValueError:
            continue
    return agg


# Şablon G (OCR'lı/taranmış raporlar): 'KOD ŞİRKET ISIN nominal değer grup% toplam%' (yüzde işareti okunmayabilir;
# OCR kod ile adı bitiştirebilir: 'TUPRSTUPRAS-TURKiYE...' → kod bilinen hisse kodlarından ayrılır)
_ROW_G = re.compile(r'^(.+?)\s+(TR[A-Z0-9]{9,10})\s+(\d[\d.,]*\d)\s+(\d[\d.,]*\d)\s+(\d[\d.,]*\d)\s*%?\s+(\d[\d.,]*\d)\s*%?$')


def _split_code(head: str, tick: set):
    """('KOD ad ...' | 'KODad...') → (kod, ad); kod bilinen hisse kodlarında değilse (None, None)."""
    parts = head.split(None, 1)
    first = parts[0]
    if first in tick:
        return first, (parts[1] if len(parts) > 1 else '')
    for n in (6, 5, 4, 3):                            # bitişik yazılmış: en uzun eşleşen kod
        if len(first) > n and first[:n] in tick and first[:n].isalnum():
            rest = first[n:] + (' ' + parts[1] if len(parts) > 1 else '')
            return first[:n], rest
    return None, None


def _parse_g(lines, num) -> dict:
    agg = {}
    tick = _tickers()
    if not tick:
        return agg
    for ln in lines:
        m = _ROW_G.match(ln.strip())
        if not m:
            continue
        head, isin, nominal, value, _grp, tot = m.groups()
        if isin.startswith('TRY'):                    # TRY... = yatırım fonu payı, hisse değil
            continue
        code, name = _split_code(head, tick)
        if not code:
            continue
        try:
            _add(agg, code, isin[:12], name, num(nominal), None, num(value), num(tot))
        except ValueError:
            continue
    return agg


def _parse_h(lines, num) -> dict:
    """Şablon H: satır başında hisse kodu + 'TL' (ticker-ilk düzen). Repo teminat satırları (değer 0) atlanır."""
    tick = _tickers()
    agg = {}
    for ln in lines:
        m = _ROW_H.match(ln.strip())
        if not m or m.group(1) not in tick:
            continue
        code, name, isin, nominal, cost, _d, _price, value, _g, _mid, tot = m.groups()
        if re.search(r'\d{2}/\d{2}/\d{2}', name or ''):
            continue
        try:
            v = num(value)
            if v <= 0:
                continue
            _add(agg, code, isin, name or '', num(nominal), num(cost), v, num(tot))
        except ValueError:
            continue
    return agg


_ROW_I = re.compile(r'^([A-Z0-9]{3,6})\s+(\d[\d.,]*)\s+(\d[\d.,]*)\s+%?\s*(\d[\d.,]*)\s*%?$')


def _parse_i(lines, num) -> dict:
    """Şablon I (Rota vb.): 'KOD nominal değer oran%' — yalnızca 4 parçalı, oranı olan satırlar; maliyet yok."""
    tick = _tickers()
    agg = {}
    for ln in lines:
        t = ln.strip()
        if '%' not in t:
            continue
        m = _ROW_I.match(t)
        if not m or m.group(1) not in tick:
            continue
        code, nominal, value, pct = m.groups()
        try:
            n_, v_, p_ = num(nominal), num(value), num(pct)
        except ValueError:
            continue
        if v_ <= 0 or not (0 < p_ <= 100):
            continue
        _add(agg, code, None, '', n_, None, v_, p_)
    return agg


_PARSERS = (_parse_a, _parse_b, _parse_c, _parse_d, _parse_e, _parse_f, _parse_g, _parse_h, _parse_i)


# ── OCR (taranmış/görüntü PDF'ler) ──────────────────────────────────────────
_ocr_lock = threading.Lock()
_ocr_engine = None


def _ocr_lines(pdf_bytes: bytes, maxpages: int = 12) -> list:
    """Metin katmanı olmayan PDF'i OCR ile okur; kutuları y konumuna göre satırlara dizer. OCR kütüphanesi yoksa []."""
    global _ocr_engine
    try:
        import numpy as np
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except Exception:                                # noqa: BLE001
        return []
    out = []
    with _heavy_lock, _pdf_lock, _ocr_lock:                   # PDFium ve OCR motoru tek iş parçacığında
        if _ocr_engine is None:
            _ocr_engine = RapidOCR()
        pdf = pdfium.PdfDocument(pdf_bytes)
        for i in range(min(maxpages, len(pdf))):
            page = pdf[i]
            w = page.get_width()
            scale = max(1.0, min(3.0, 1900.0 / w))   # ~1900 px genişlik: hızlı ve okunaklı
            img = page.render(scale=scale).to_pil().convert('RGB')
            page.close()
            arr = np.array(img)
            del img
            res, _ = _ocr_engine(arr)
            del arr
            boxes = []
            for b in (res or []):
                ys = [pt[1] for pt in b[0]]
                xs = [pt[0] for pt in b[0]]
                boxes.append({'y': sum(ys) / 4, 'h': max(ys) - min(ys), 'x': min(xs), 't': b[1].strip()})
            boxes.sort(key=lambda b: b['y'])
            row, last_y, hs = [], None, [b['h'] for b in boxes] or [10]
            tol = 0.6 * sorted(hs)[len(hs) // 2]
            for b in boxes:
                if last_y is not None and abs(b['y'] - last_y) > tol:
                    out.append(' '.join(x['t'] for x in sorted(row, key=lambda z: z['x'])))
                    row = []
                row.append(b)
                last_y = b['y'] if not row[:-1] else last_y
            if row:
                out.append(' '.join(x['t'] for x in sorted(row, key=lambda z: z['x'])))
        pdf.close()
    import gc
    gc.collect()
    return out


def _plumber_lines(pdf_bytes: bytes, maxpages: int = 40) -> list:
    """İkinci okuma: sayfadaki konuma göre satır kurar (sütun sütun yazılmış PDF'lerde satırlar doğru gelir)."""
    try:
        import pdfplumber
    except ImportError:
        return []
    out = []
    try:
        with _heavy_lock, pdfplumber.open(io.BytesIO(pdf_bytes)) as p:
            for pg in p.pages[:maxpages]:
                out += (pg.extract_text() or '').split('\n')
                pg.flush_cache()                     # sayfa nesneleri birikip belleği şişirmesin
    except Exception:                                # noqa: BLE001
        return []
    import gc
    gc.collect()
    return out


def _looks_columnar(lines) -> bool:
    """Hisse kodları tek başına satır satır dizilmişse (sütun düzeni) ikinci okuma değer."""
    tick = _tickers()
    return sum(1 for l in lines if l.strip() in tick or (l.split()[:1] and l.split()[0] in tick)) >= 3


def _run_parsers(lines, num) -> dict:
    for fn in _PARSERS:
        try:
            agg = fn(lines, num)
        except Exception:                           # noqa: BLE001 — bir şablon çökse de diğerleri denensin
            agg = {}
        if agg:
            return agg
    return {}


def parse_report(pdf_bytes: bytes) -> dict:
    """Hisse (PAY) pozisyonlarını hisse bazında toplar; şablonlar sırayla denenir (ilk sonuç veren kazanır)."""
    lines = _pdf_lines(pdf_bytes)
    num = _doc_num(lines)
    agg = _run_parsers(lines, num)
    if not agg and sum(len(l) for l in lines) < 300:  # metin katmanı yok (taranmış görüntü): OCR
        ol = _ocr_lines(pdf_bytes)
        if ol:
            lines = ol
            num = _doc_num(lines)
            agg = _run_parsers(lines, num)
    elif not agg and _looks_columnar(lines):        # sütun düzeni: konuma göre satır kurup yeniden dene
        pl = _plumber_lines(pdf_bytes)
        if pl:
            lines = pl
            num = _doc_num(lines)
            agg = _run_parsers(lines, num)
    holdings = []
    for a in agg.values():
        a['avg_cost'] = a['cost_x_lot'] / a['cost_lots'] if a['cost_lots'] else None
        del a['cost_x_lot'], a['cost_lots']
        holdings.append(a)
    holdings.sort(key=lambda x: -x['weight_pct'])
    return {'fund_value': _fund_value(lines, num), 'stock_pct': sum(h['weight_pct'] for h in holdings), 'holdings': holdings}


def parse_disclosure(disclosure_index: int) -> dict:
    """Duyurudaki PDF eklerini sırayla dener (bazı fonlar portföy tablosunu ikinci ekte yollar); hisse çıkan ilk ek kazanır."""
    best, fund_value = None, None
    for oid in _pdf_ids(disclosure_index):
        r = parse_report(_get(f'api/file/download/{oid}', raw=True))
        if r['fund_value'] and fund_value is None:
            fund_value = r['fund_value']
        if r['holdings']:
            if r['fund_value'] is None:
                r['fund_value'] = fund_value
            return r
        best = best or r
    if best is not None and best['fund_value'] is None:
        best['fund_value'] = fund_value
    return best


# ── Depolama ────────────────────────────────────────────────────────────────
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(SCHEMA)
    for ddl in ('ALTER TABLE kap_funds ADD COLUMN fund_class TEXT',
                'ALTER TABLE kap_reports ADD COLUMN parse_ver INTEGER DEFAULT 1',
                'ALTER TABLE kap_funds ADD COLUMN hist_checked TEXT'):
        try:
            con.execute(ddl)
        except sqlite3.OperationalError:
            pass                                     # sütun zaten var
    # klasmanı olmayan fonları doldur
    rows = con.execute('SELECT code, name FROM kap_funds WHERE fund_class IS NULL').fetchall()
    if rows:
        con.executemany('UPDATE kap_funds SET fund_class=? WHERE code=?', [(classify(n), c) for c, n in rows])
        con.commit()
    return con


def save_report(con, code: str, rep: dict, parsed: dict):
    con.execute('DELETE FROM kap_holdings WHERE fund_code=? AND period=?', (code, rep['period']))
    con.execute('INSERT OR REPLACE INTO kap_reports (fund_code, period, disclosure_index, published, fund_value, stock_pct, parse_ver) VALUES (?,?,?,?,?,?,?)',
                (code, rep['period'], rep['index'], rep['published'], parsed['fund_value'], parsed['stock_pct'], PARSER_VERSION))
    con.executemany('INSERT INTO kap_holdings VALUES (?,?,?,?,?,?,?,?,?)',
                    [(code, rep['period'], h['stock'], h['isin'], h['name'], h['lots'], h['value'], h['weight_pct'], h['avg_cost'])
                     for h in parsed['holdings']])


def discover(con, log=print):
    """Fon listesini KAP'tan alır; yeni fonları ekler (mevcut kimlik bilgilerini korur)."""
    n_new = 0
    for t in FUND_TYPES:
        for f in list_funds(t):
            status = 'skipped' if SKIP_NAME.search(f['name']) else None
            cur = con.execute('INSERT OR IGNORE INTO kap_funds (code,name,type,permalink,status,fund_class) VALUES (?,?,?,?,?,?)',
                              (f['code'], f['name'], t, f['permalink'], status, classify(f['name'])))
            n_new += cur.rowcount
        con.commit()
    log(f'fon listesi: {n_new} yeni fon eklendi')


def _process_fund(fund: dict, days: int, have: set, max_new=None) -> dict:
    """Ağ + PDF işi (iş parçacığında). DB yazımı çağıran tarafta."""
    res = {'code': fund['code'], 'oid': fund['oid'], 'subject_oid': fund['subject_oid'], 'status': 'ok',
           'reports': [], 'errors': []}
    try:
        if not fund['oid'] or not fund['subject_oid']:
            oid, subj = resolve_fund(fund['permalink'])
            res['oid'], res['subject_oid'] = oid, subj
            if not oid or not subj:
                res['status'] = 'no_report'
                return res
        new = [r for r in fund_reports(res['oid'], res['subject_oid'], days) if r['period'] not in have]
        if max_new:
            new = new[:max_new]                      # en yeni max_new rapor (liste yeniden eskiye sıralı)
        for rep in new:
            try:
                res['reports'].append((rep, parse_disclosure(rep['index'])))
            except NoPdf:
                continue                             # muaf fon: hata değil, atla
            except Exception as e:                   # noqa: BLE001
                res['errors'].append(f"{rep['period']}: {str(e)[:80]}")
    except Exception as e:                           # noqa: BLE001
        res['status'] = 'error'
        res['errors'].append(str(e)[:120])
    return res


def run(days=130, budget_min=60, workers=2, only_codes=None, log=print, skip_checked_today=False, do_discover=True,
        max_new=None, mark_hist=False):
    con = connect()
    if do_discover:
        discover(con, log)
    today = date.today().isoformat()
    q = "SELECT code,name,type,permalink,oid,subject_oid,status,last_checked FROM kap_funds WHERE COALESCE(status,'') NOT IN ('skipped')"
    funds = [dict(zip(('code', 'name', 'type', 'permalink', 'oid', 'subject_oid', 'status', 'last_checked'), r))
             for r in con.execute(q)]
    if only_codes:
        funds = [f for f in funds if f['code'] in only_codes]

    def _prio(f):                       # hisse taşıması en olası fonlar önce işlensin
        n = (f['name'] or '').upper().replace('İ', 'I')
        return 0 if 'HISSE' in n else (1 if any(k in n for k in ('DEGISKEN', 'KARMA', 'SERBEST', 'ENDEKS')) else 2)
    funds.sort(key=lambda f: (_prio(f), f['code']))
    if skip_checked_today:
        funds = [f for f in funds if f['last_checked'] != today]
    have_by = {}
    for c, p in con.execute('SELECT fund_code, period FROM kap_reports'):
        have_by.setdefault(c, set()).add(p)

    deadline = time.time() + budget_min * 60
    done = new_reports = errs = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        it = iter(funds)
        pending = set()

        def submit_next():
            if time.time() > deadline or not _mem_ok(log):
                return False
            f = next(it, None)
            if f is None:
                return False
            pending.add(ex.submit(_process_fund, f, days, have_by.get(f['code'], set()), max_new))
            return True

        for _ in range(workers * 2):
            if not submit_next():
                break
        while pending:
            for fut in as_completed(list(pending)):
                pending.discard(fut)
                r = fut.result()
                if r['status'] != 'error' or r['oid']:
                    con.execute('UPDATE kap_funds SET oid=?, subject_oid=?, status=?, last_checked=? WHERE code=?',
                                (r['oid'], r['subject_oid'], r['status'], today, r['code']))
                    if mark_hist and r['status'] == 'ok' and not r['errors']:      # hatalı fon işaretlenmez, bir sonraki turda yeniden denenir
                        con.execute('UPDATE kap_funds SET hist_checked=? WHERE code=?', (today, r['code']))
                for rep, parsed in r['reports']:
                    save_report(con, r['code'], rep, parsed)
                    new_reports += 1
                errs += len(r['errors'])
                for e in r['errors'][:2]:
                    log(f"  ! {r['code']}: {e}")
                con.commit()
                done += 1
                if done % 25 == 0:
                    log(f'[{(time.time() - t0) / 60:.1f} dk] {done}/{len(funds)} fon, {new_reports} yeni rapor, {errs} hata')
                submit_next()
                break
    con.close()
    left = len(funds) - done
    log(f'bitti: {done} fon işlendi, {new_reports} yeni rapor, {errs} hata' + (f', {left} fon kaldı (süre doldu)' if left > 0 else ''))
    return left


def reparse(budget_min=45, workers=2, log=print) -> int:
    """
    Eski ayrıştırıcı sürümüyle hisse çıkarılamayan raporları (hisse satırı olmayan) yeniden indirip dener.
    Yine boş çıkarsa parse_ver güncellenir; böylece aynı rapor tekrar tekrar denenmez.
    """
    con = connect()
    skip = ','.join('?' * len(_NO_STOCK_CLASSES))
    rows = con.execute(f"""
        SELECT r.fund_code, r.period, r.disclosure_index, r.published
        FROM kap_reports r JOIN kap_funds f ON f.code = r.fund_code
        WHERE COALESCE(r.parse_ver, 1) < ?
          AND NOT EXISTS (SELECT 1 FROM kap_holdings h WHERE h.fund_code = r.fund_code AND h.period = r.period)
          AND COALESCE(f.fund_class, '') NOT IN ({skip})
        ORDER BY r.period DESC, r.fund_code""", (PARSER_VERSION, *_NO_STOCK_CLASSES)).fetchall()
    log(f'yeniden ayrıştırılacak rapor: {len(rows)}')
    deadline = time.time() + budget_min * 60
    done = fixed = 0

    def work(row):
        code, period, index, published = row
        try:
            return row, parse_disclosure(index), None
        except Exception as e:                       # noqa: BLE001
            return row, None, str(e)[:100]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        it = iter(rows)
        pending = set()

        def nxt():
            if time.time() > deadline or not _mem_ok(log):
                return False
            r = next(it, None)
            if r is None:
                return False
            pending.add(ex.submit(work, r))
            return True

        for _ in range(workers * 2):
            if not nxt():
                break
        while pending:
            for fut in as_completed(list(pending)):
                pending.discard(fut)
                (code, period, index, published), parsed, err = fut.result()
                if parsed is not None:
                    save_report(con, code, {'period': period, 'index': index, 'published': published}, parsed)
                    fixed += bool(parsed['holdings'])
                else:
                    log(f'  ! {code} {period}: {err}')
                con.commit()
                done += 1
                if done % 25 == 0:
                    log(f'reparse {done}/{len(rows)}, {fixed} rapor düzeldi')
                nxt()
                break
    con.close()
    log(f'reparse bitti: {done} rapor denendi, {fixed} tanesinde hisse bulundu, {len(rows) - done} kaldı')
    return len(rows) - done


def history(budget_min=60, workers=2, log=print) -> int:
    """
    Seyrek (üç aylık vb.) raporlayan fonlar: son iki aydır hiç raporu olmayan 'ok' fonların 400 günlük geçmişinden
    en yeni 3 raporu alınır; sayfada "son rapor" olarak gösterilir. Bir fon ayda bir yeniden denenir (hist_checked).
    """
    per = expected_period()
    y, m = int(per[:4]), int(per[5:])
    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    per_prev = f'{y}-{m:02d}'
    con = connect()
    stale = [c for (c,) in con.execute("""
        SELECT f.code FROM kap_funds f
        WHERE f.status = 'ok'
          AND (f.hist_checked IS NULL OR f.hist_checked < date('now', '-30 day'))
          AND COALESCE((SELECT MAX(period) FROM kap_reports r WHERE r.fund_code = f.code), '') < ?""", (per_prev,))]
    con.close()
    log(f'geçmiş raporu çekilecek (seyrek raporlayan) fon: {len(stale)}')
    if not stale:
        return 0
    return run(days=365, budget_min=budget_min, workers=workers, only_codes=set(stale), log=log, do_discover=False,
               max_new=3, mark_hist=True)


def expected_period(today=None) -> str:
    """Bugün itibarıyla tamamlanmış olması beklenen son rapor ayı.
    Raporlar ayın ~8'inde yayımlanır; ayın 12'sinden itibaren geçen ay beklenir, öncesinde bir önceki ay."""
    d = today or date.today()
    back = 1 if d.day >= 12 else 2
    y, m = d.year, d.month - back
    while m < 1:
        y, m = y - 1, m + 12
    return f'{y}-{m:02d}'


def monthly(budget_min=240, workers=2, log=print) -> int:
    """
    PA görevi (saatlik çalıştırılabilir, kendini tamamlar): beklenen dönem için raporu olmayan fonları kontrol eder.
      • tek örnek kilidi: süreç ölürse bir sonraki çalıştırma kaldığı yerden devam eder
      • ayın 12'sinde yeni ay başlar; kalan fonlar ertesi günlerde kaldığı yerden devam eder
      • hepsi tamamsa hiç istek atmadan çıkar
    Fon listesi ayın 12-13'ünde (ve tablo boşsa) KAP'tan tazelenir.
    """
    if not _acquire_lock():
        log('başka bir kap_funds monthly süreci çalışıyor, çıkılıyor')
        return 0
    try:
        return _monthly(budget_min, workers, log)
    finally:
        _release_lock()


LOCK_PATH = os.path.join(_ROOT, 'data', 'kap_monthly.lock')


def _acquire_lock() -> bool:
    """Tek örnek kilidi: pid dosyası; ölü bir sürecin kilidi (kill/konsol kapanması) devralınır."""
    try:
        with open(LOCK_PATH) as f:
            pid = int(f.read().strip() or 0)
        if pid and pid != os.getpid():
            try:
                os.kill(pid, 0)
                return False                     # süreç yaşıyor
            except OSError:
                pass                             # ölü süreç → kilidi devral
    except (OSError, ValueError):
        pass
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, 'w') as f:
        f.write(str(os.getpid()))
    return True


def _release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def _monthly(budget_min, workers, log) -> int:
    today = date.today()
    per = expected_period(today)
    con = connect()
    empty = con.execute('SELECT COUNT(*) FROM kap_funds').fetchone()[0] == 0
    if empty or today.day in (12, 13):
        discover(con, log)
    have = {c for (c,) in con.execute('SELECT fund_code FROM kap_reports WHERE period=?', (per,))}
    todo = [c for (c,) in con.execute("SELECT code FROM kap_funds WHERE COALESCE(status,'') NOT IN ('skipped','no_report')")
            if c not in have]
    con.close()
    log(f'{today} hedef dönem {per}: {len(have)} fonun raporu var, {len(todo)} fon kontrol edilecek')
    t0 = time.time()
    left = 0
    if todo:
        # Aynı gün zaten kontrol edilen fonları (raporu henüz yayımlanmamış olabilir) yeniden sorgulama
        left = run(days=70, budget_min=budget_min, workers=workers, only_codes=set(todo), log=log, skip_checked_today=True,
                   do_discover=False)
    remaining = budget_min - (time.time() - t0) / 60
    if left == 0 and remaining > 1:                       # seyrek raporlayan fonların geçmişi
        left = history(budget_min=remaining, workers=workers, log=log)
        remaining = budget_min - (time.time() - t0) / 60
    if left == 0 and remaining > 1:                       # bekleyen fon kalmadıysa boş kalan raporları yeniden ayrıştır
        left = reparse(budget_min=remaining, workers=workers, log=log)
    return left


def status() -> dict:
    con = connect()
    try:
        return {
            'funds': dict(con.execute('SELECT COALESCE(status,"pending"), COUNT(*) FROM kap_funds GROUP BY 1').fetchall()),
            'reports': con.execute('SELECT COUNT(*), COUNT(DISTINCT fund_code), MIN(period), MAX(period) FROM kap_reports').fetchone(),
            'by_period': con.execute('SELECT period, COUNT(*) FROM kap_reports GROUP BY 1 ORDER BY 1 DESC LIMIT 6').fetchall(),
            'holdings': con.execute('SELECT COUNT(*) FROM kap_holdings').fetchone()[0],
        }
    finally:
        con.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['discover', 'run', 'monthly', 'fund', 'status', 'reparse', 'history'])
    ap.add_argument('arg', nargs='?')
    ap.add_argument('--days', type=int, default=130)
    ap.add_argument('--budget', type=int, default=60, help='dakika')
    ap.add_argument('--workers', type=int, default=2)
    a = ap.parse_args()
    if a.cmd in ('run', 'history', 'reparse', 'fund') and not _acquire_lock():
        print('başka bir kap_funds süreci çalışıyor (data/kap_monthly.lock), çıkılıyor')
        sys.exit(0)
    if a.cmd == 'discover':
        c = connect()
        discover(c)
        print(status())
    elif a.cmd == 'run':
        run(a.days, a.budget, a.workers)
        print(status())
    elif a.cmd == 'monthly':
        monthly(a.budget, a.workers)
    elif a.cmd == 'history':
        history(a.budget, a.workers)
    elif a.cmd == 'reparse':
        reparse(a.budget, a.workers)
    elif a.cmd == 'fund':
        run(a.days, a.budget, 1, only_codes={a.arg.upper()})
        print(status())
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1, default=str))
