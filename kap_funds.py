# -*- coding: utf-8 -*-
"""
KAP fon "Portföy Dağılım Raporu" çekici + ayrıştırıcı (kamuya açık KAP uç noktaları).

Akış:
  1) fon listesi   : GET /tr/YatirimFonlari/<TİP>  (sayfaya gömülü JSON: fundOid, fundCode, permaLink)
  2) fon raporları : GET /tr/api/disclosure/filter/FILTERYFBF/<fundOid>/<konuOid>/<gün>
  3) ek dosya      : GET /tr/api/notification/attachment-detail/<disclosureIndex>  -> objId
  4) PDF           : GET /tr/api/file/download/<objId>
  5) ayrıştırma    : PAY bölümü (hisse) satırları -> hisse bazında ağırlık

Sonuçlar data/kap_fund_holdings.db'ye yazılır (gitignore). Sunucuya yük bindirmemek için istekler arasında bekler.

CLI:
    python kap_funds.py fund MAC            # tek fonun son 365 gündeki raporlarını çek + kaydet
    python kap_funds.py status
"""
import html
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

BASE = 'https://www.kap.org.tr/tr'
SUBJECT_PORTFOY_DAGILIM = '8aca490d502e34b801502e380044002b'   # "Portföy Dağılım Raporu"
HEADERS = {'User-Agent': 'Mozilla/5.0 (3nfinans fon takip)', 'Accept-Language': 'tr'}
DELAY = 0.8

_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_ROOT, 'data', 'kap_fund_holdings.db')

SCHEMA = """
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
"""


def _get(path: str, raw=False, tries=3):
    url = path if path.startswith('http') else f'{BASE}/{path}'
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
                data = r.read()
            time.sleep(DELAY)
            return data if raw else json.loads(data.decode('utf-8'))
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f'{url}: {last}')


def list_funds(fund_type: str = 'YF') -> list:
    """KAP fon listesi: [{'code','oid','permalink','name','type'}]. fund_type: YF, EYF, BYF, ..."""
    raw = _get(f'https://www.kap.org.tr/tr/YatirimFonlari/{fund_type}', raw=True).decode('utf-8', 'ignore')
    s = html.unescape(raw).replace('\\"', '"')
    out, seen = [], set()
    for m in re.finditer(r'"fundOid":"([0-9A-F]{32})","fundId":"\d+","fundCode":"([A-Z0-9]+)","fundName":"([^"]*)"', s):
        oid, code, name = m.groups()
        if oid not in seen:
            seen.add(oid)
            out.append({'code': code, 'oid': oid, 'name': name, 'type': fund_type})
    return out


def fund_reports(fund_oid: str, days: int = 365) -> list:
    """Fonun 'Portföy Dağılım Raporu' bildirimleri (yeniden eskiye)."""
    items = _get(f'api/disclosure/filter/FILTERYFBF/{fund_oid}/{SUBJECT_PORTFOY_DAGILIM}/{days}')
    res = []
    for it in items:
        b = it['disclosureBasic']
        res.append({'index': b['disclosureIndex'], 'published': b['publishDate'], 'year': b.get('year'),
                    'month': b.get('donem'), 'summary': b.get('summary'), 'attachments': b.get('attachmentCount')})
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


def parse_report(pdf_bytes: bytes) -> dict:
    """PAY (hisse) satırlarını hisse bazında toplar. Döner: {'fund_value','stock_pct','holdings':[...]}."""
    import pdfplumber
    lines = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as p:
        for pg in p.pages:
            lines += (pg.extract_text() or '').split('\n')

    fund_value = None
    for ln in lines:
        m = re.search(r'TOPLAM DE.ER: \(TL\) ([\d.,]+)', ln)
        if m:
            fund_value = _num(m.group(1))
            break

    agg = {}
    for ln in lines:
        t = ln.split()
        if len(t) >= 12 and re.match(r'^[A-Z0-9]+\.[A-Z]$', t[0]) and t[-1].endswith('%') and t[-2].endswith('%'):
            isin = next((x for x in t if re.match(r'^TR[A-Z0-9]{10}$', x)), None)
            if not isin:
                continue
            code = t[0].split('.')[0]
            try:
                nominal, cost, value, pct = _num(t[-9]), _num(t[-8][:-10]), _num(t[-3]), _num(t[-1])
            except ValueError:
                continue
            a = agg.setdefault(code, {'stock': code, 'isin': isin, 'name': ' '.join(t[1:t.index(isin)]),
                                      'lots': 0.0, 'value': 0.0, 'weight_pct': 0.0, 'cost_x_lot': 0.0})
            a['lots'] += nominal
            a['value'] += value
            a['weight_pct'] += pct
            a['cost_x_lot'] += cost * nominal
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
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def save(con, code: str, period: str, rep: dict, parsed: dict):
    con.execute('DELETE FROM kap_holdings WHERE fund_code=? AND period=?', (code, period))
    con.execute('INSERT OR REPLACE INTO kap_reports VALUES (?,?,?,?,?,?)',
                (code, period, rep['index'], rep['published'], parsed['fund_value'], parsed['stock_pct']))
    con.executemany('INSERT INTO kap_holdings VALUES (?,?,?,?,?,?,?,?,?)',
                    [(code, period, h['stock'], h['isin'], h['name'], h['lots'], h['value'], h['weight_pct'], h['avg_cost'])
                     for h in parsed['holdings']])


def ingest_fund(code: str, oid: str, days: int = 365, only_new: bool = True) -> list:
    con = connect()
    done = {r[0] for r in con.execute('SELECT period FROM kap_reports WHERE fund_code=?', (code,))}
    log = []
    try:
        for rep in fund_reports(oid, days):
            if not rep['year'] or not rep['month']:
                continue
            period = f"{rep['year']}-{int(rep['month']):02d}"
            if only_new and period in done:
                continue
            try:
                parsed = parse_report(report_pdf(rep['index']))
                save(con, code, period, rep, parsed)
                con.commit()
                log.append((period, len(parsed['holdings']), round(parsed['stock_pct'], 2)))
            except Exception as e:                   # noqa: BLE001
                log.append((period, 'HATA', str(e)[:80]))
    finally:
        con.close()
    return log


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'fund':
        code = sys.argv[2].upper()
        fund = next((f for t in ('YF', 'EYF', 'BYF') for f in list_funds(t) if f['code'] == code), None)
        if not fund:
            sys.exit(f'{code}: KAP fon listesinde bulunamadı')
        for row in ingest_fund(code, fund['oid']):
            print(row)
    elif len(sys.argv) >= 2 and sys.argv[1] == 'status':
        con = connect()
        print(con.execute('SELECT fund_code, COUNT(*), MIN(period), MAX(period) FROM kap_reports GROUP BY 1').fetchall())
    else:
        print(__doc__)
