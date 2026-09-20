# -*- coding: utf-8 -*-
"""
BofA net akışı ↔ BİST 100 getirisi korelasyon analizi (saf Python, numpy/scipy gerekmez).

Akış ölçüsü: günlük net TL / o günkü BofA toplam hacmi (alış+satış). Hacim büyüdükçe artan
nominal TL'nin sonucu bozmaması için oranlanır. Fiyat seviyesi ile kümülatif net doğrudan
kıyaslanmaz (ikisi de trendli → sahte korelasyon); getiri ile akış karşılaştırılır.

BİST 100 kapanışları TradingView'dan (collect_bist.fetch_tv_ws) çekilip data/xu100_daily.json'da
önbelleğe alınır.
"""
import json
import math
import os
from datetime import date, datetime, timedelta

import bofa_db

_ROOT = os.path.dirname(os.path.abspath(__file__))
XU100_PATH = os.path.join(_ROOT, 'data', 'xu100_daily.json')


# ── BİST 100 fiyatı ──────────────────────────────────────────────────────────
def _load_xu100() -> dict:
    try:
        with open(XU100_PATH, encoding='utf-8') as f:
            x = json.load(f)
        return dict(zip(x['dates'], x['closes']))
    except Exception:
        return {}


def get_xu100(need_until: str) -> dict:
    """{'YYYY-MM-DD': kapanış}. Önbellek eskiyse TradingView'dan tazelemeyi dener."""
    px = _load_xu100()
    if px and max(px) >= need_until:
        return px
    try:
        import collect_bist
        dates, closes = collect_bist.fetch_tv_ws('XU100', 'BIST', 1200, '1D')
        if dates:
            os.makedirs(os.path.dirname(XU100_PATH), exist_ok=True)
            with open(XU100_PATH, 'w', encoding='utf-8') as f:
                json.dump({'dates': dates, 'closes': closes}, f)
            return dict(zip(dates, closes))
    except Exception:
        pass
    return px


# ── İstatistik yardımcıları ──────────────────────────────────────────────────
def _pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a)
    sb = sum((y - mb) ** 2 for y in b)
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sa * sb)


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def _pval(r, n):
    """İki yönlü p (t → normal yaklaşımı; n≥30 için yeterli)."""
    if r is None or n < 4:
        return None
    if abs(r) >= 1:
        return 0.0
    t = abs(r) * math.sqrt((n - 2) / (1 - r * r))
    return math.erfc(t / math.sqrt(2))


def _corr(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 4:
        return {'n': n, 'pearson': None, 'spearman': None, 'p': None}
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    r = _pearson(xs, ys)
    s = _pearson(_ranks(xs), _ranks(ys))
    return {'n': n, 'pearson': r, 'spearman': s, 'p': _pval(r, n)}


def _shift(v, k):
    """corr(v_t, w_{t+k}) için w'yi geri kaydırır: sonuç[i] = v[i+k]."""
    n = len(v)
    return [v[i + k] if 0 <= i + k < n else None for i in range(n)]


# ── Ana hesap ────────────────────────────────────────────────────────────────
def compute(include_funds: bool = False) -> dict:
    con = bofa_db.connect()
    try:
        scope = '' if include_funds else " WHERE code NOT LIKE '%.F'"
        rows = con.execute(
            'SELECT trade_date, SUM(net_volume), SUM(buy_volume), SUM(sell_volume) '
            'FROM bofa_daily' + scope + ' GROUP BY trade_date ORDER BY trade_date').fetchall()
    finally:
        con.close()
    if not rows:
        return {'empty': True}

    px = get_xu100(rows[-1][0])
    pdates = sorted(px)
    ret_by_date = {}
    for i in range(1, len(pdates)):
        p0, p1 = px[pdates[i - 1]], px[pdates[i]]
        if p0:
            ret_by_date[pdates[i]] = p1 / p0 - 1

    days = []                      # BofA günü + getirisi olanlar
    for d, net, b, s in rows:
        if d in ret_by_date and (b or 0) + (s or 0) > 0:
            days.append({'d': d, 'net': net or 0.0, 'gross': (b or 0) + (s or 0),
                         'ratio': (net or 0.0) / ((b or 0) + (s or 0)), 'ret': ret_by_date[d]})
    n = len(days)
    if n < 10:
        return {'empty': True}

    ratio = [x['ratio'] for x in days]
    ret = [x['ret'] for x in days]
    netv = [x['net'] for x in days]

    out = {
        'empty': False, 'include_funds': include_funds, 'n': n,
        'first': days[0]['d'], 'last': days[-1]['d'],
        'same_day': _corr(ratio, ret),
        'same_day_tl': _corr(netv, ret),
        'next_day': _corr(ratio, _shift(ret, 1)),      # BofA(t) → getiri(t+1)
        'prev_day': _corr(ratio, _shift(ret, -1)),     # getiri(t-1) → BofA(t)
    }

    out['lags'] = []
    for k in range(-5, 6):
        c = _corr(ratio, _shift(ret, k))
        out['lags'].append({'k': k, 'r': c['pearson'], 'n': c['n']})

    # Haftalık / aylık
    def bucket(keyfn):
        agg = {}
        for x in days:
            k = keyfn(x['d'])
            a = agg.setdefault(k, [0.0, 0.0])
            a[0] += x['net']
            a[1] += x['gross']
        last_px = {}
        for d in pdates:
            last_px[keyfn(d)] = px[d]
        keys = sorted(last_px)
        pret = {keys[i]: last_px[keys[i]] / last_px[keys[i - 1]] - 1 for i in range(1, len(keys))}
        ks = [k for k in sorted(agg) if k in pret and agg[k][1] > 0]
        return ([agg[k][0] / agg[k][1] for k in ks], [pret[k] for k in ks])

    def wk(d):
        y, w, _ = datetime.strptime(d, '%Y-%m-%d').isocalendar()
        return f'{y}-{w:02d}'

    wr, wp = bucket(wk)
    out['weekly'] = _corr(wr, wp)
    out['weekly_next'] = _corr(wr, _shift(wp, 1))
    mr, mp = bucket(lambda d: d[:7])
    out['monthly'] = _corr(mr, mp)

    # Seviye (sahte korelasyon uyarısı için)
    cum, run = [], 0.0
    for x in days:
        run += x['net']
        cum.append(run)
    out['level'] = _corr(cum, [px[x['d']] for x in days])

    # Kayan 60 gün
    W = 60
    roll = []
    for i in range(W - 1, n):
        r = _pearson(ratio[i - W + 1:i + 1], ret[i - W + 1:i + 1])
        if r is not None:
            roll.append({'d': days[i]['d'], 'r': r})
    out['rolling'] = roll

    # Serpilme (%): x = net/hacim, y = getiri; regresyon doğrusu
    out['scatter'] = [[round(a * 100, 3), round(b * 100, 3)] for a, b in zip(ratio, ret)]
    mx, my = sum(ratio) / n, sum(ret) / n
    sxx = sum((a - mx) ** 2 for a in ratio)
    slope = sum((a - mx) * (b - my) for a, b in zip(ratio, ret)) / sxx if sxx else 0
    out['fit'] = {'slope': slope, 'intercept': my - slope * mx,
                  'xmin': min(ratio) * 100, 'xmax': max(ratio) * 100}

    # Ondalık dilimler: akışa göre 10 gruba böl → ortalama aynı gün / ertesi gün getiri
    order = sorted(range(n), key=lambda i: ratio[i])
    dec = []
    for g in range(10):
        idx = order[g * n // 10:(g + 1) * n // 10]
        same = [ret[i] for i in idx]
        nxt = [ret[i + 1] for i in idx if i + 1 < n]
        dec.append({'g': g + 1, 'n': len(idx),
                    'ratio': sum(ratio[i] for i in idx) / len(idx),
                    'same': sum(same) / len(same),
                    'next': (sum(nxt) / len(nxt)) if nxt else None})
    out['deciles'] = dec

    # Yön isabeti: BofA net alıcı gün ↔ endeks yükseldi mi
    up = [(x['net'] > 0) == (x['ret'] > 0) for x in days if x['ret'] != 0 and x['net'] != 0]
    out['direction_hit'] = sum(up) / len(up) if up else None
    return out


# ── Trend (rejim) analizi ────────────────────────────────────────────────────
WINDOWS = {'1h': ('1 hafta', 5), '2h': ('2 hafta', 10), '1a': ('1 ay', 21)}
HORIZONS = (1, 5, 10, 21)          # ileri getiri ufukları (işlem günü)


def _welch_p(a, b):
    if len(a) < 5 or len(b) < 5:
        return None
    def mv(v):
        m = sum(v) / len(v)
        return m, sum((x - m) ** 2 for x in v) / (len(v) - 1)
    ma, va = mv(a)
    mb, vb = mv(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return None
    return math.erfc(abs(ma - mb) / se / math.sqrt(2))


def _mean(v):
    return sum(v) / len(v) if v else None


def trend(include_funds: bool = False) -> dict:
    """
    Rejim: son N işlem gününün net akış toplamı > 0 → 'alım yönlü trend', < 0 → 'satım yönlü'.
    N = 5 (1 hafta), 10 (2 hafta), 21 (1 ay). Rejim gün sonunda bilinir; ileri getiri o günün
    kapanışından hesaplanır (ileriye bakma yok).
    """
    con = bofa_db.connect()
    try:
        scope = '' if include_funds else " WHERE code NOT LIKE '%.F'"
        rows = con.execute(
            'SELECT trade_date, SUM(net_volume) FROM bofa_daily' + scope +
            ' GROUP BY trade_date ORDER BY trade_date').fetchall()
    finally:
        con.close()
    if not rows:
        return {'empty': True}
    px = get_xu100(rows[-1][0])
    days = [(d, n or 0.0, px[d]) for d, n in rows if d in px]
    n = len(days)
    if n < 60:
        return {'empty': True}
    dates = [x[0] for x in days]
    net = [x[1] for x in days]
    price = [x[2] for x in days]

    out = {'empty': False, 'include_funds': include_funds, 'n': n,
           'first': dates[0], 'last': dates[-1], 'price': price, 'dates': dates, 'windows': {}}

    for key, (label, N) in WINDOWS.items():
        wsum = [None] * n
        run = sum(net[:N])
        for i in range(N - 1, n):
            if i >= N:
                run += net[i] - net[i - N]
            elif i == N - 1:
                run = sum(net[:N])
            wsum[i] = run
        regime = [None if s is None else (1 if s > 0 else (-1 if s < 0 else 0)) for s in wsum]

        # Bölümler (aynı rejimin ardışık günleri)
        eps, i = [], N - 1
        while i < n:
            j = i
            while j + 1 < n and regime[j + 1] == regime[i]:
                j += 1
            eps.append({'s': dates[i], 'e': dates[j], 'r': regime[i], 'len': j - i + 1})
            i = j + 1

        cur = eps[-1]
        info = {
            'label': label, 'N': N,
            'current': {'regime': regime[-1], 'window_net': wsum[-1], 'streak': cur['len'],
                        'since': cur['s'], 'date': dates[-1]},
            'days_buy': sum(1 for r in regime if r == 1),
            'days_sell': sum(1 for r in regime if r == -1),
            'episodes': eps,
            'avg_len_buy': _mean([e['len'] for e in eps if e['r'] == 1]),
            'avg_len_sell': _mean([e['len'] for e in eps if e['r'] == -1]),
            'wsum': wsum,
            'regime': regime,
        }

        # Geriye dönük: rejimin sürdüğü pencerede endeks getirisi (aynı pencere)
        past = {1: [], -1: []}
        for i in range(N, n):
            if regime[i] in (1, -1):
                past[regime[i]].append(price[i] / price[i - N] - 1)
        info['past'] = {'buy': _mean(past[1]), 'sell': _mean(past[-1]),
                        'buy_hit': (sum(1 for v in past[1] if v > 0) / len(past[1])) if past[1] else None,
                        'sell_hit': (sum(1 for v in past[-1] if v < 0) / len(past[-1])) if past[-1] else None}

        # İleri getiri (rejim → sonraki h gün), ayrıca örtüşmesiz örneklem ile p
        fwd = []
        for h in HORIZONS:
            buy_all, sell_all, buy_no, sell_no = [], [], [], []
            for i in range(N - 1, n - h):
                r = price[i + h] / price[i] - 1
                if regime[i] == 1:
                    buy_all.append(r)
                elif regime[i] == -1:
                    sell_all.append(r)
                if (i - (N - 1)) % h == 0:               # örtüşmesiz alt örneklem
                    (buy_no if regime[i] == 1 else sell_no if regime[i] == -1 else []).append(r)
            base = _mean(buy_all + sell_all)
            fwd.append({
                'h': h,
                'buy_mean': _mean(buy_all), 'buy_n': len(buy_all),
                'buy_hit': (sum(1 for v in buy_all if v > 0) / len(buy_all)) if buy_all else None,
                'sell_mean': _mean(sell_all), 'sell_n': len(sell_all),
                'sell_hit': (sum(1 for v in sell_all if v > 0) / len(sell_all)) if sell_all else None,
                'base': base,
                'diff': (_mean(buy_all) - _mean(sell_all)) if buy_all and sell_all else None,
                'p_nonoverlap': _welch_p(buy_no, sell_no), 'n_nonoverlap': (len(buy_no), len(sell_no)),
            })
        info['forward'] = fwd
        out['windows'][key] = info
    return out
