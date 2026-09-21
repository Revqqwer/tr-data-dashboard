# -*- coding: utf-8 -*-
"""
Fon İçerikleri sayfası için sorgular (data/kap_fund_holdings.db — kap_funds.py'nin doldurduğu KAP verisi).

Aylık değişim hesabı yalnızca HER İKİ dönemde de raporu olan fonlar üzerinden yapılır
(raporu eksik fon, "sattı" ya da "aldı" gibi görünmesin diye).
"""
import os
import sqlite3

try:
    from kap_funds import classify
except Exception:                                    # noqa: BLE001
    def classify(name):
        return 'Diğer'

_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_ROOT, 'data', 'kap_fund_holdings.db')


def _con():
    if not os.path.exists(DB_PATH):
        return None
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _class_map(con) -> dict:
    """{fon kodu: klasman} — fon adından hesaplanır (DB şemasından bağımsız)."""
    return {r['code']: classify(r['name']) for r in con.execute('SELECT code, name FROM kap_funds')}


def _month_add(period: str, delta: int) -> str:
    y, m = int(period[:4]), int(period[5:])
    t = y * 12 + (m - 1) + delta
    return f'{t // 12}-{t % 12 + 1:02d}'


def _asof(con, per: str, max_age_months: int = 12) -> dict:
    """{fon: seçili dönem itibarıyla en son rapor dönemi} (en fazla max_age_months eski)."""
    lo = _month_add(per, -(max_age_months - 1))
    return {r[0]: r[1] for r in con.execute(
        'SELECT fund_code, MAX(period) FROM kap_reports WHERE period<=? AND period>=? GROUP BY fund_code', (per, lo))}


def _prev_reports(con) -> dict:
    """{fon: [dönemler, yeniden eskiye]}"""
    out = {}
    for r in con.execute('SELECT fund_code, period FROM kap_reports ORDER BY period DESC'):
        out.setdefault(r[0], []).append(r[1])
    return out


def _prev_of(plist, period):
    for p in plist:
        if p < period:
            return p
    return None


def _stock_detail_asof(code, period):
    con = _con()
    if not con:
        return {'empty': True}
    try:
        periods = _periods(con)
        per = period if period in periods else (periods[0] if periods else None)
        if not per:
            return {'empty': True}
        asof = _asof(con, per)
        plist = _prev_reports(con)
        names = {r['code']: r['name'] for r in con.execute('SELECT code, name FROM kap_funds')}
        rows = con.execute('SELECT fund_code, period, lots, value, weight_pct, avg_cost FROM kap_holdings WHERE stock=?', (code,)).fetchall()
        by = {}
        for r in rows:
            by.setdefault((r['fund_code'], r['period']), r)
        funds, exited = [], []
        for fc, ap in asof.items():
            cur = by.get((fc, ap))
            pp = _prev_of(plist.get(fc, []), ap)
            prv = by.get((fc, pp)) if pp else None
            if cur:
                pl = prv['lots'] if prv else 0
                funds.append({'fund': fc, 'name': names.get(fc), 'fclass': classify(names.get(fc)), 'weight': cur['weight_pct'],
                              'lots': cur['lots'], 'value': cur['value'], 'avg_cost': cur['avg_cost'], 'rperiod': ap, 'prev_rperiod': pp,
                              'prev_lots': pl if pp else None, 'delta_lots': (cur['lots'] - pl) if pp else None,
                              'status': None if not pp else ('new' if not pl else ('up' if cur['lots'] > pl else ('down' if cur['lots'] < pl else 'same')))})
            elif prv:
                exited.append({'fund': fc, 'name': names.get(fc), 'prev_lots': prv['lots'], 'rperiod': ap})
        funds.sort(key=lambda f: -(f['value'] or 0))
        series = [dict(r) for r in con.execute(
            'SELECT period, COUNT(*) funds, SUM(lots) lots, SUM(value) value FROM kap_holdings WHERE stock=? GROUP BY period ORDER BY period', (code,))]
        nm = con.execute('SELECT name FROM kap_holdings WHERE stock=? ORDER BY LENGTH(name) DESC LIMIT 1', (code,)).fetchone()
        return {'empty': False, 'scope': 'asof', 'code': code, 'name': nm[0] if nm else None, 'period': per, 'prev': None,
                'periods': periods, 'funds': funds, 'exited': exited, 'series': series}
    finally:
        con.close()


def _top_moves_asof(period, limit, cls, q):
    """Her fonun seçili dönem itibarıyla SON raporu ile bir önceki raporu arasındaki lot değişimi (aylık + seyrek raporlayanlar)."""
    con = _con()
    if not con:
        return {'empty': True}
    try:
        periods = _periods(con)
        per = period if period in periods else (periods[0] if periods else None)
        if not per:
            return {'empty': True, 'periods': periods}
        asof = _asof(con, per)
        plist = _prev_reports(con)
        cmap = _class_map(con) if cls else {}
        pairs = {}
        for fc, ap in asof.items():
            if cls and cmap.get(fc, 'Diğer') != cls:
                continue
            pp = _prev_of(plist.get(fc, []), ap)
            if pp:
                pairs[fc] = (ap, pp)
        if not pairs:
            return {'empty': True, 'periods': periods}
        need = {(fc, ap) for fc, (ap, pp) in pairs.items()} | {(fc, pp) for fc, (ap, pp) in pairs.items()}
        cur, prv, price = {}, {}, {}
        for r in con.execute('SELECT fund_code, period, stock, lots, value FROM kap_holdings'):
            key = (r['fund_code'], r['period'])
            if key not in need:
                continue
            ap, pp = pairs[r['fund_code']]
            (cur if r['period'] == ap else prv).setdefault(r['stock'], {})[r['fund_code']] = r['lots']
            if r['lots'] and r['value'] and (r['period'] == ap or r['stock'] not in price):
                price[r['stock']] = r['value'] / r['lots']
        names = {r['stock']: r['name'] for r in con.execute('SELECT stock, MAX(name) name FROM kap_holdings GROUP BY stock')}
        qq = (q or '').strip().upper()
        out = []
        for st in set(cur) | set(prv):
            if qq and qq not in st and qq not in (names.get(st) or '').upper():
                continue
            px = price.get(st)
            if not px:
                continue
            c, p_ = cur.get(st, {}), prv.get(st, {})
            n_new = n_up = n_down = n_exit = 0
            d_lots = cur_l = prev_l = 0.0
            for fc in set(c) | set(p_):
                a, b = c.get(fc, 0.0), p_.get(fc, 0.0)
                cur_l += a
                prev_l += b
                d_lots += a - b
                if a > b and b == 0:
                    n_new += 1
                elif a > b:
                    n_up += 1
                elif a < b and a > 0:
                    n_down += 1
                elif a == 0 and b > 0:
                    n_exit += 1
            out.append({'code': st, 'name': names.get(st), 'cur_lots': cur_l, 'prev_lots': prev_l, 'd_lots': d_lots, 'd_tl': d_lots * px,
                        'price': px, 'n_new': n_new, 'n_up': n_up, 'n_down': n_down, 'n_exit': n_exit, 'n_hold': sum(1 for v in c.values() if v > 0)})
        bought = sorted([x for x in out if x['d_tl'] > 0], key=lambda x: -x['d_tl'])[:limit]
        sold = sorted([x for x in out if x['d_tl'] < 0], key=lambda x: x['d_tl'])[:limit]
        stale = sum(1 for fc, (ap, pp) in pairs.items() if ap != per)
        return {'empty': False, 'scope': 'asof', 'period': per, 'prev': None, 'periods': periods, 'comparable_funds': len(pairs),
                'stale_funds': stale, 'cls': cls, 'q': qq,
                'total_buy': sum(x['d_tl'] for x in out if x['d_tl'] > 0), 'total_sell': sum(x['d_tl'] for x in out if x['d_tl'] < 0),
                'bought': bought, 'sold': sold}
    finally:
        con.close()


def _class_funds_asof(cls, period):
    con = _con()
    if not con or not cls:
        return {'empty': True}
    try:
        periods = _periods(con)
        per = period if period in periods else (periods[0] if periods else None)
        cmap = _class_map(con)
        total = skipped = no_report = 0
        for r in con.execute("SELECT code, COALESCE(status,'') st FROM kap_funds"):
            if cmap.get(r['code'], 'Diğer') != cls:
                continue
            total += 1
            skipped += r['st'] == 'skipped'
            no_report += r['st'] == 'no_report'
        funds = []
        if per:
            asof = _asof(con, per)
            for r in con.execute('SELECT f.code, f.name FROM kap_funds f'):
                if cmap.get(r['code'], 'Diğer') != cls or r['code'] not in asof:
                    continue
                ap = asof[r['code']]
                rep = con.execute('SELECT fund_value, stock_pct FROM kap_reports WHERE fund_code=? AND period=?', (r['code'], ap)).fetchone()
                nh = con.execute('SELECT COUNT(*) FROM kap_holdings WHERE fund_code=? AND period=?', (r['code'], ap)).fetchone()[0]
                funds.append({'code': r['code'], 'name': r['name'], 'holdings': nh, 'stock_pct': rep['stock_pct'] if rep else None,
                              'fund_value': rep['fund_value'] if rep else None, 'rperiod': ap})
        funds.sort(key=lambda x: (-(x['holdings'] > 0), -(x['fund_value'] or 0)))
        return {'empty': False, 'scope': 'asof', 'cls': cls, 'period': per, 'total': total, 'skipped': skipped, 'no_report': no_report,
                'with_report': len(funds), 'with_stocks': sum(1 for f in funds if f['holdings'] > 0), 'funds': funds}
    finally:
        con.close()


def _prev(periods: list, period: str):
    """periods: yeniden eskiye sıralı; verilen dönemin bir önceki ayı (listede varsa)."""
    y, m = int(period[:4]), int(period[5:])
    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    p = f'{y}-{m:02d}'
    return p if p in periods else None


def months() -> dict:
    con = _con()
    if not con:
        return {'periods': []}
    try:
        rows = con.execute('SELECT period, COUNT(*) n FROM kap_reports GROUP BY period ORDER BY period DESC').fetchall()
        cmap = _class_map(con)
        latest = rows[0]['period'] if rows else None
        stat = {}
        for r in con.execute('SELECT code, COALESCE(status, \'\') st FROM kap_funds'):
            k = cmap.get(r['code'], 'Diğer')
            d = stat.setdefault(k, {'total': 0, 'with_report': 0, 'with_stocks': 0})
            d['total'] += 1
        if latest:
            for r in con.execute('SELECT DISTINCT fund_code FROM kap_reports WHERE period=?', (latest,)):
                stat.setdefault(cmap.get(r[0], 'Diğer'), {'total': 0, 'with_report': 0, 'with_stocks': 0})['with_report'] += 1
            for r in con.execute('SELECT DISTINCT fund_code FROM kap_holdings WHERE period=?', (latest,)):
                stat.setdefault(cmap.get(r[0], 'Diğer'), {'total': 0, 'with_report': 0, 'with_stocks': 0})['with_stocks'] += 1
        if latest:                                                # son rapor (as-of) sayaçları
            asof = _asof(con, latest)
            hold = {(r[0], r[1]) for r in con.execute('SELECT DISTINCT fund_code, period FROM kap_holdings')}
            for fc, ap in asof.items():
                d = stat.setdefault(cmap.get(fc, 'Diğer'), {'total': 0, 'with_report': 0, 'with_stocks': 0})
                d['with_report_asof'] = d.get('with_report_asof', 0) + 1
                if (fc, ap) in hold:
                    d['with_stocks_asof'] = d.get('with_stocks_asof', 0) + 1
        for v in stat.values():
            v.setdefault('with_report_asof', 0)
            v.setdefault('with_stocks_asof', 0)
        classes = [{'name': k, **v, 'funds': v['with_stocks']} for k, v in sorted(stat.items(), key=lambda x: (-x[1]['with_stocks_asof'], -x[1]['total']))]
        return {'periods': [{'period': r['period'], 'funds': r['n']} for r in rows], 'classes': classes}
    finally:
        con.close()


def coverage() -> dict:
    """Kapsam özeti: KAP'ta taranan fonlar ve rapor sayıları (sayfadaki bilgi notu için)."""
    con = _con()
    if not con:
        return {}
    try:
        st = dict(con.execute("SELECT COALESCE(status,'pending'), COUNT(*) FROM kap_funds GROUP BY 1").fetchall())
        with_rep = con.execute('SELECT COUNT(DISTINCT fund_code) FROM kap_reports').fetchone()[0]
        with_stock = con.execute('SELECT COUNT(DISTINCT fund_code) FROM kap_holdings').fetchone()[0]
        return {'status': st, 'funds_total': sum(st.values()), 'funds_with_reports': with_rep, 'funds_with_stocks': with_stock}
    finally:
        con.close()


def _periods(con) -> list:
    return [r[0] for r in con.execute('SELECT DISTINCT period FROM kap_reports ORDER BY period DESC')]


def stock_list(period: str = None) -> list:
    """Seçili dönemde en az bir fonun taşıdığı hisseler (arama listesi): kod, ad, fon sayısı."""
    con = _con()
    if not con:
        return []
    try:
        per = period or (_periods(con) or [None])[0]
        rows = con.execute('SELECT stock, name, COUNT(*) OVER (PARTITION BY stock) n FROM kap_holdings WHERE period=?', (per,)).fetchall()
        best = {}
        for r in rows:                                   # şablonlara göre ad kısa/uzun gelir: en uzununu al
            b = best.get(r['stock'])
            if not b or len(r['name'] or '') > len(b['name'] or ''):
                best[r['stock']] = {'code': r['stock'], 'name': r['name'], 'funds': r['n']}
        return sorted(best.values(), key=lambda x: -x['funds'])
    finally:
        con.close()


def stock_detail(code: str, period: str = None, scope: str = 'monthly') -> dict:
    """Bir hisseyi hangi fonlar taşıyor + önceki aya göre değişim + aylık toplam seri."""
    code = (code or '').strip().upper()
    if scope == 'asof':
        return _stock_detail_asof(code, period)
    con = _con()
    if not con:
        return {'empty': True}
    try:
        periods = _periods(con)
        per = period if period in periods else (periods[0] if periods else None)
        if not per:
            return {'empty': True}
        prev = _prev(periods, per)
        rows = con.execute(
            'SELECT h.fund_code, f.name fname, f.type ftype, h.lots, h.value, h.weight_pct, h.avg_cost '
            'FROM kap_holdings h LEFT JOIN kap_funds f ON f.code=h.fund_code '
            'WHERE h.stock=? AND h.period=? ORDER BY h.value DESC', (code, per)).fetchall()
        prev_lots = {}
        prev_has = set()
        if prev:
            prev_has = {r[0] for r in con.execute('SELECT fund_code FROM kap_reports WHERE period=?', (prev,))}
            prev_lots = {r['fund_code']: r['lots'] for r in con.execute(
                'SELECT fund_code, lots FROM kap_holdings WHERE stock=? AND period=?', (code, prev))}
        funds = []
        for r in rows:
            pl = prev_lots.get(r['fund_code'])
            comparable = r['fund_code'] in prev_has
            funds.append({
                'fund': r['fund_code'], 'name': r['fname'], 'fclass': classify(r['fname']), 'weight': r['weight_pct'], 'lots': r['lots'],
                'value': r['value'], 'avg_cost': r['avg_cost'],
                'prev_lots': pl if comparable else None,
                'delta_lots': (r['lots'] - (pl or 0)) if comparable else None,
                'status': None if not comparable else ('new' if not pl else ('up' if r['lots'] > pl else ('down' if r['lots'] < pl else 'same'))),
            })
        # Önceki ay taşıyıp bu ay çıkanlar
        exited = []
        if prev:
            cur = {r['fund_code'] for r in rows}
            cur_has = {r[0] for r in con.execute('SELECT fund_code FROM kap_reports WHERE period=?', (per,))}
            for fc, pl in prev_lots.items():
                if fc not in cur and fc in cur_has:
                    nm = con.execute('SELECT name FROM kap_funds WHERE code=?', (fc,)).fetchone()
                    exited.append({'fund': fc, 'name': nm[0] if nm else None, 'prev_lots': pl})
        series = [dict(r) for r in con.execute(
            'SELECT period, COUNT(*) funds, SUM(lots) lots, SUM(value) value FROM kap_holdings WHERE stock=? GROUP BY period ORDER BY period', (code,))]
        name = con.execute('SELECT name FROM kap_holdings WHERE stock=? ORDER BY LENGTH(name) DESC LIMIT 1', (code,)).fetchone()
        name = name[0] if name else None
        return {'empty': False, 'code': code, 'name': name, 'period': per, 'prev': prev, 'periods': periods,
                'funds': funds, 'exited': exited, 'series': series}
    finally:
        con.close()


def top_moves(period: str = None, limit: int = 25, cls: str = None, q: str = None, scope: str = 'monthly') -> dict:
    """
    Dönem içinde fonların en çok aldığı / sattığı hisseler.
    Yalnızca iki dönemde de raporu olan fonlar; değişim = Σ (lot_bu_ay − lot_önceki_ay); TL karşılığı bu ayın ima edilen fiyatı ile.
    """
    if scope == 'asof':
        return _top_moves_asof(period, limit, cls, q)
    con = _con()
    if not con:
        return {'empty': True}
    try:
        periods = _periods(con)
        per = period if period in periods else (periods[0] if periods else None)
        prev = _prev(periods, per) if per else None
        if not per or not prev:
            return {'empty': True, 'periods': periods}
        both = [r[0] for r in con.execute(
            'SELECT a.fund_code FROM kap_reports a JOIN kap_reports b ON a.fund_code=b.fund_code AND b.period=? WHERE a.period=?', (prev, per))]
        if cls:                                          # yalnızca seçili klasmandaki fonlar
            cmap = _class_map(con)
            both = [c for c in both if cmap.get(c, 'Diğer') == cls]
        con.execute('CREATE TEMP TABLE both(fund_code TEXT PRIMARY KEY)')
        con.executemany('INSERT INTO both VALUES (?)', [(c,) for c in both])
        rows = con.execute("""
            SELECT stock, MAX(name) name,
                   SUM(cur_lots) cur_lots, SUM(prev_lots) prev_lots,
                   SUM(cur_lots - prev_lots) d_lots,
                   SUM(CASE WHEN cur_value > 0 AND cur_lots > 0 THEN (cur_lots - prev_lots) * (cur_value / cur_lots) END) d_tl_partial,
                   MAX(price) price,
                   SUM(cur_lots > prev_lots AND prev_lots = 0) n_new,
                   SUM(cur_lots > prev_lots AND prev_lots > 0) n_up,
                   SUM(cur_lots < prev_lots AND cur_lots > 0) n_down,
                   SUM(cur_lots = 0 AND prev_lots > 0) n_exit,
                   SUM(cur_lots > 0) n_hold
            FROM (
              SELECT fund_code, stock, name,
                     SUM(CASE WHEN period=? THEN lots ELSE 0 END) cur_lots,
                     SUM(CASE WHEN period=? THEN lots ELSE 0 END) prev_lots,
                     SUM(CASE WHEN period=? THEN value ELSE 0 END) cur_value,
                     SUM(CASE WHEN period=? THEN value ELSE 0 END) prev_value,
                     MAX(CASE WHEN period=? AND lots>0 THEN value/lots END) price
              FROM kap_holdings
              WHERE period IN (?,?) AND fund_code IN (SELECT fund_code FROM both)
              GROUP BY fund_code, stock
            ) GROUP BY stock
        """, (per, prev, per, prev, per, per, prev)).fetchall()
        # Fiyatı olmayanlar (bu ay hiç kimse taşımıyor): önceki aydaki ima edilen fiyatla
        pp = {r[0]: r[1] for r in con.execute(
            'SELECT stock, MAX(value/lots) FROM kap_holdings WHERE period=? AND lots>0 GROUP BY stock', (prev,))}
        out = []
        for r in rows:
            price = r['price'] or pp.get(r['stock'])
            if not price:
                continue
            out.append({'code': r['stock'], 'name': r['name'], 'cur_lots': r['cur_lots'], 'prev_lots': r['prev_lots'],
                        'd_lots': r['d_lots'], 'd_tl': r['d_lots'] * price, 'price': price,
                        'n_new': r['n_new'], 'n_up': r['n_up'], 'n_down': r['n_down'], 'n_exit': r['n_exit'], 'n_hold': r['n_hold']})
        qq = (q or '').strip().upper()
        if qq:                                           # hisse filtresi: kod ya da ad içinde arar
            out = [x for x in out if qq in x['code'] or qq in (x['name'] or '').upper()]
        bought = sorted([x for x in out if x['d_tl'] > 0], key=lambda x: -x['d_tl'])[:limit]
        sold = sorted([x for x in out if x['d_tl'] < 0], key=lambda x: x['d_tl'])[:limit]
        return {'empty': False, 'period': per, 'prev': prev, 'periods': periods, 'comparable_funds': len(both), 'cls': cls, 'q': qq,
                'total_buy': sum(x['d_tl'] for x in out if x['d_tl'] > 0),
                'total_sell': sum(x['d_tl'] for x in out if x['d_tl'] < 0),
                'bought': bought, 'sold': sold}
    finally:
        con.close()


def class_funds(cls: str, period: str = None, scope: str = 'monthly') -> dict:
    """Bir sınıftaki fonlar: KAP'taki toplam, raporu olanlar ve hisse portföyü okunanlar (liste + sayaçlar)."""
    if scope == 'asof':
        return _class_funds_asof(cls, period)
    con = _con()
    if not con or not cls:
        return {'empty': True}
    try:
        periods = _periods(con)
        per = period if period in periods else (periods[0] if periods else None)
        cmap = _class_map(con)
        total = skipped = no_report = 0
        for r in con.execute("SELECT code, COALESCE(status,'') st FROM kap_funds"):
            if cmap.get(r['code'], 'Diğer') != cls:
                continue
            total += 1
            skipped += r['st'] == 'skipped'
            no_report += r['st'] == 'no_report'
        funds = []
        if per:
            rows = con.execute(
                "SELECT f.code, f.name, r.fund_value, r.stock_pct, "
                "(SELECT COUNT(*) FROM kap_holdings h WHERE h.fund_code=f.code AND h.period=?) nh "
                "FROM kap_funds f JOIN kap_reports r ON r.fund_code=f.code AND r.period=?", (per, per)).fetchall()
            for r in rows:
                if cmap.get(r['code'], 'Diğer') != cls:
                    continue
                funds.append({'code': r['code'], 'name': r['name'], 'holdings': r['nh'], 'stock_pct': r['stock_pct'], 'fund_value': r['fund_value']})
        funds.sort(key=lambda x: (-(x['holdings'] > 0), -(x['fund_value'] or 0)))
        return {'empty': False, 'cls': cls, 'period': per, 'total': total, 'skipped': skipped, 'no_report': no_report,
                'with_report': len(funds), 'with_stocks': sum(1 for f in funds if f['holdings'] > 0), 'funds': funds}
    finally:
        con.close()


def fund_search(q: str, limit: int = 30, cls: str = None) -> list:
    """Kod/ad araması; klasman verilirse o klasmandaki fonlar (arama boşsa da listelenir)."""
    q = (q or '').strip()
    con = _con()
    if not con or (not q and not cls):
        return []
    try:
        like = f'%{q.upper()}%'
        rows = con.execute(
            "SELECT f.code, f.name, f.type, (SELECT MAX(period) FROM kap_reports r WHERE r.fund_code=f.code) last, "
            "(SELECT COUNT(*) FROM kap_holdings h WHERE h.fund_code=f.code AND h.period=(SELECT MAX(period) FROM kap_reports r2 WHERE r2.fund_code=f.code)) nh "
            "FROM kap_funds f WHERE (UPPER(f.code) LIKE ? OR UPPER(f.name) LIKE ?) "
            "AND EXISTS (SELECT 1 FROM kap_reports r WHERE r.fund_code=f.code) ORDER BY (UPPER(f.code)=?) DESC, nh DESC, f.code",
            (like, like, q.upper())).fetchall()
        out = []
        for r in rows:
            c = classify(r['name'])
            if cls and c != cls:
                continue
            out.append({'code': r['code'], 'name': r['name'], 'type': r['type'], 'last': r['last'], 'fclass': c, 'holdings': r['nh']})
            if len(out) >= limit:
                break
        return out
    finally:
        con.close()


def fund_detail(code: str, period: str = None) -> dict:
    code = (code or '').strip().upper()
    con = _con()
    if not con:
        return {'empty': True}
    try:
        f = con.execute('SELECT code, name, type FROM kap_funds WHERE code=?', (code,)).fetchone()
        rep = con.execute('SELECT period, fund_value, stock_pct, published FROM kap_reports WHERE fund_code=? ORDER BY period DESC', (code,)).fetchall()
        if not f or not rep:
            return {'empty': True}
        periods = [r['period'] for r in rep]
        per = period if period in periods else periods[0]
        prev = _prev(periods, per)
        cur = con.execute('SELECT stock, name, lots, value, weight_pct, avg_cost FROM kap_holdings WHERE fund_code=? AND period=? ORDER BY value DESC', (code, per)).fetchall()
        pl = {}
        if prev:
            pl = {r['stock']: r['lots'] for r in con.execute('SELECT stock, lots FROM kap_holdings WHERE fund_code=? AND period=?', (code, prev))}
        holdings = []
        for r in cur:
            p = pl.get(r['stock']) if prev else None
            holdings.append({'code': r['stock'], 'name': r['name'], 'lots': r['lots'], 'value': r['value'], 'weight': r['weight_pct'],
                             'avg_cost': r['avg_cost'], 'prev_lots': p if prev else None,
                             'delta_lots': (r['lots'] - (p or 0)) if prev else None,
                             'status': None if not prev else ('new' if not p else ('up' if r['lots'] > p else ('down' if r['lots'] < p else 'same')))})
        exited = []
        if prev:
            cs = {r['stock'] for r in cur}
            for s, l in pl.items():
                if s not in cs:
                    exited.append({'code': s, 'prev_lots': l})
        weights = {}
        for r in con.execute('SELECT period, stock, weight_pct FROM kap_holdings WHERE fund_code=?', (code,)):
            weights.setdefault(r['stock'], {})[r['period']] = r['weight_pct']
        return {'empty': False, 'fund': {'code': f['code'], 'name': f['name'], 'type': f['type'], 'fclass': classify(f['name'])}, 'period': per, 'prev': prev,
                'periods': periods, 'reports': [dict(r) for r in rep], 'holdings': holdings, 'exited': exited, 'weights': weights}
    finally:
        con.close()
