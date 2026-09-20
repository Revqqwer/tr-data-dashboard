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
    items = _get(f'api/disclosure/filter/FILTERYFBF/{oid}/{subject}/{days}')
    res = []
    for it in items:
        b = it['disclosureBasic']
        # Yalnızca aylık raporlar (haftalık raporların 'dönem' numarası 13+ olur)
        if b.get('title') != 'Portföy Dağılım Raporu' or not b.get('year') or not b.get('donem') or int(b['donem']) > 12:
            continue
        res.append({'index': b['disclosureIndex'], 'published': b['publishDate'],
                    'period': f"{b['year']}-{int(b['donem']):02d}"})
    return res


def report_pdf(disclosure_index: int) -> bytes:
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
        raise RuntimeError(f'{disclosure_index}: PDF eki yok')
    return _get(f'api/file/download/{ids[0]}', raw=True)


# ── PDF ayrıştırma ──────────────────────────────────────────────────────────
def _num(s: str) -> float:
    return float(s.replace('.', '').replace(',', '.').replace('%', ''))


_pdf_lock = threading.Lock()      # PDFium iş parçacığı güvenli DEĞİL (çoklu iş parçacığında çöker)


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
_ISIN = re.compile(r'^TR[A-Z0-9]{9}\d$')
_tickers_cache = None


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
                              'weight_pct': 0.0, 'cost_x_lot': 0.0})
    if isin and not a['isin']:
        a['isin'] = isin
    a['lots'] += nominal
    a['value'] += value
    a['weight_pct'] += pct
    a['cost_x_lot'] += cost * nominal


def _parse_a(lines) -> dict:
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
            nominal, cost = _num(t[di - 2]), _num(t[di - 1])
            value, pct = _num(t[-3]), _num(t[-1])
        except ValueError:
            continue
        _add(agg, t[0].split('.')[0], isin, ' '.join(t[1:t.index(isin)]), nominal, cost, value, pct)
    return agg


def _parse_b(lines) -> dict:
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
        _add(agg, cur, isin, cur_name, _num(nominal), _num(cost), _num(value), _num(tot))
    return agg


def parse_report(pdf_bytes: bytes) -> dict:
    """Hisse (PAY) pozisyonlarını hisse bazında toplar; şablon A, olmazsa B denenir."""
    lines = _pdf_lines(pdf_bytes)
    fund_value = None
    for ln in lines:
        m = re.search(r'TOPLAM DE.ER:\s*\(TL\)\s*([\d.,]+)', ln)
        if m:
            fund_value = _num(m.group(1))
            break
    agg = _parse_a(lines) or _parse_b(lines)
    holdings = []
    for a in agg.values():
        a['avg_cost'] = a['cost_x_lot'] / a['lots'] if a['lots'] else None
        del a['cost_x_lot']
        holdings.append(a)
    holdings.sort(key=lambda x: -x['weight_pct'])
    return {'fund_value': fund_value, 'stock_pct': sum(h['weight_pct'] for h in holdings), 'holdings': holdings}


# ── Depolama ────────────────────────────────────────────────────────────────
def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(SCHEMA)
    return con


def save_report(con, code: str, rep: dict, parsed: dict):
    con.execute('DELETE FROM kap_holdings WHERE fund_code=? AND period=?', (code, rep['period']))
    con.execute('INSERT OR REPLACE INTO kap_reports VALUES (?,?,?,?,?,?)',
                (code, rep['period'], rep['index'], rep['published'], parsed['fund_value'], parsed['stock_pct']))
    con.executemany('INSERT INTO kap_holdings VALUES (?,?,?,?,?,?,?,?,?)',
                    [(code, rep['period'], h['stock'], h['isin'], h['name'], h['lots'], h['value'], h['weight_pct'], h['avg_cost'])
                     for h in parsed['holdings']])


def discover(con, log=print):
    """Fon listesini KAP'tan alır; yeni fonları ekler (mevcut kimlik bilgilerini korur)."""
    n_new = 0
    for t in FUND_TYPES:
        for f in list_funds(t):
            status = 'skipped' if SKIP_NAME.search(f['name']) else None
            cur = con.execute('INSERT OR IGNORE INTO kap_funds (code,name,type,permalink,status) VALUES (?,?,?,?,?)',
                              (f['code'], f['name'], t, f['permalink'], status))
            n_new += cur.rowcount
        con.commit()
    log(f'fon listesi: {n_new} yeni fon eklendi')


def _process_fund(fund: dict, days: int, have: set) -> dict:
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
        for rep in fund_reports(res['oid'], res['subject_oid'], days):
            if rep['period'] in have:
                continue
            try:
                res['reports'].append((rep, parse_report(report_pdf(rep['index']))))
            except Exception as e:                   # noqa: BLE001
                res['errors'].append(f"{rep['period']}: {str(e)[:80]}")
    except Exception as e:                           # noqa: BLE001
        res['status'] = 'error'
        res['errors'].append(str(e)[:120])
    return res


def run(days=130, budget_min=60, workers=2, only_codes=None, log=print, skip_checked_today=False, do_discover=True):
    con = connect()
    if do_discover:
        discover(con, log)
    today = date.today().isoformat()
    q = "SELECT code,name,type,permalink,oid,subject_oid,status,last_checked FROM kap_funds WHERE COALESCE(status,'') NOT IN ('skipped')"
    funds = [dict(zip(('code', 'name', 'type', 'permalink', 'oid', 'subject_oid', 'status', 'last_checked'), r))
             for r in con.execute(q)]
    if only_codes:
        funds = [f for f in funds if f['code'] in only_codes]
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
            if time.time() > deadline:
                return False
            f = next(it, None)
            if f is None:
                return False
            pending.add(ex.submit(_process_fund, f, days, have_by.get(f['code'], set())))
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
    PA günlük görevi (kendini tamamlar): beklenen dönem için raporu olmayan fonları kontrol eder.
      • ayın 12'sinde yeni ay başlar; kalan fonlar ertesi günlerde kaldığı yerden devam eder
      • hepsi tamamsa hiç istek atmadan çıkar
    Fon listesi ayın 12-13'ünde (ve tablo boşsa) KAP'tan tazelenir.
    """
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
    if not todo:
        return 0
    # Aynı gün zaten kontrol edilen fonları (raporu henüz yayımlanmamış olabilir) yeniden sorgulama
    return run(days=70, budget_min=budget_min, workers=workers, only_codes=set(todo), log=log, skip_checked_today=True,
               do_discover=False)


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
    ap.add_argument('cmd', choices=['discover', 'run', 'monthly', 'fund', 'status'])
    ap.add_argument('arg', nargs='?')
    ap.add_argument('--days', type=int, default=130)
    ap.add_argument('--budget', type=int, default=60, help='dakika')
    ap.add_argument('--workers', type=int, default=2)
    a = ap.parse_args()
    if a.cmd == 'discover':
        c = connect()
        discover(c)
        print(status())
    elif a.cmd == 'run':
        run(a.days, a.budget, a.workers)
        print(status())
    elif a.cmd == 'monthly':
        monthly(a.budget, a.workers)
    elif a.cmd == 'fund':
        run(a.days, a.budget, 1, only_codes={a.arg.upper()})
        print(status())
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1, default=str))
