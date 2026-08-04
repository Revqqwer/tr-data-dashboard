# -*- coding: utf-8 -*-
"""
Endeks bileşenlerinin getiri istatistiği (ortalama / medyan / dağılım).

Kullanım (PA'da repo kökünden):
    python scripts/index_stats.py                       # XTUMY, 5 yıl
    python scripts/index_stats.py --index XU100 --period 3y
    python scripts/index_stats.py --index XTUMY --period 5y --top 15
    python scripts/index_stats.py --list                # mevcut endeksleri listele

Veri kaynağı: data/bist_cache.db  →  stock_returns (collect_bist.py doldurur).

ÖNEMLİ — "kısa geçmişli" hisseler:
collect_bist.py dönem dilimlemesini `min(istenen_bar, mevcut_bar)` ile yapar.
Yani 5 yıldan genç bir hissenin "5y" getirisi aslında sadece borsada olduğu
süreyi ölçer (ör. 8 aylık bir halka arzın "5 yıllık getirisi" = 8 aylık getirisi).
Bu, ortalamayı yukarı çeker. Bar sayısı yetmediğinde 5y ve 10y dilimleri aynı
sonucu verdiği için, `5y == 10y` olan hisseleri "tam geçmişi yok" diye
işaretliyoruz ve istatistiği hem ham hem de temizlenmiş halde veriyoruz.
"""
import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

# Windows konsolu (cp1254) Türkçe olmayan sembollerde patlıyor; çıktıyı UTF-8'e sabitle
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'data' / 'bist_cache.db'

# Ticker (XTUMY) → panel adı (BIST TUM-100) çözümü için
try:
    sys.path.insert(0, str(BASE_DIR))
    from collect_bist import ENDEKSLER          # {panel_adi: ticker}
except Exception:
    ENDEKSLER = {}

# Bu dönem, bar sayısı yetmediğinde dilimin "kırpıldığını" anlamak için referans
FULL_HISTORY_REF = '10y'


def resolve_index(conn, wanted: str) -> str:
    """Kullanıcının yazdığını (XTUMY / 'BIST TUM-100') DB'deki index_name'e çevirir."""
    names = [r[0] for r in conn.execute(
        'SELECT DISTINCT index_name FROM stock_returns ORDER BY index_name')]
    if not names:
        sys.exit('HATA: stock_returns tablosu boş. Önce: python collect_bist.py')

    w = wanted.strip().upper()
    for n in names:                                    # birebir panel adı
        if n.upper() == w:
            return n
    for panel, ticker in ENDEKSLER.items():            # ticker üzerinden
        if ticker.upper() == w and panel in names:
            return panel
    hits = [n for n in names if w in n.upper()]        # kısmi eşleşme
    if len(hits) == 1:
        return hits[0]

    print(f'"{wanted}" bulunamadı. Mevcut endeksler:\n')
    for n in names:
        print(f'  {ENDEKSLER.get(n, "?"):<8} {n}')
    sys.exit(1)


def load_period(conn, index_name: str, period: str):
    """{kod: {"ad":.., "pct":..}} döner; pct None olanlar dahil."""
    row = conn.execute(
        'SELECT data FROM stock_returns WHERE index_name=? AND period=?',
        (index_name, period)).fetchone()
    if not row:
        sys.exit(f'HATA: {index_name} / {period} için kayıt yok.')
    return {s['kod']: s for s in json.loads(row[0])}


def index_own_return(conn, index_name: str, period: str):
    row = conn.execute(
        'SELECT pct FROM index_history WHERE name=? AND period=?',
        (index_name, period)).fetchone()
    return row[0] if row else None


def describe(vals, baslik):
    """Bir getiri listesinin özeti."""
    if not vals:
        print(f'\n{baslik}: veri yok')
        return
    v = sorted(vals)
    n = len(v)
    ort = statistics.mean(v)
    med = statistics.median(v)
    art = sum(1 for x in v if x > 0)

    print(f'\n{baslik}  (n = {n})')
    print(f'  {"ORTALAMA":<14} % {ort:>10.2f}')
    print(f'  {"MEDYAN":<14} % {med:>10.2f}')
    print(f'  {"-"*32}')
    print(f'  {"En düşük":<14} % {v[0]:>10.2f}')
    print(f'  {"Q1 (%25)":<14} % {v[n//4]:>10.2f}')
    print(f'  {"Q3 (%75)":<14} % {v[(3*n)//4]:>10.2f}')
    print(f'  {"En yüksek":<14} % {v[-1]:>10.2f}')
    if n > 1:
        print(f'  {"Std. sapma":<14} % {statistics.stdev(v):>10.2f}')
    print(f'  {"Yükselen":<14} {art:>5} / {n}  (%{art/n*100:.1f})')
    print(f'  {"Düşen":<14} {n-art:>5} / {n}  (%{(n-art)/n*100:.1f})')
    return ort, med


# Getiri aralıkları (%) — hisse SAYISINA değil, getiri BÜYÜKLÜĞÜNE göre sınıflama
DEFAULT_BANDS = [0, 100, 250, 500, 1000, 2500, 5000]


def _tr(x):
    """1234.0 → '1.234' (Türkçe binlik ayracı)"""
    return f'{x:,.0f}'.replace(',', '.')


def print_bands(rows, own, period, edges):
    """Hisseleri getiri ARALIKLARINA göre sınıflandırır (eşit sayı değil)."""
    vals = sorted(p for _, _, p in rows)
    n = len(vals)
    if not n:
        return
    lims = [float('-inf')] + list(edges) + [float('inf')]

    gruplar = []
    for i in range(len(lims) - 1):
        lo, hi = lims[i], lims[i + 1]
        g = [v for v in vals if lo <= v < hi]
        if lo == float('-inf'):
            ad = 'Zarar (< %0)'
        elif hi == float('inf'):
            ad = f'%{_tr(lo)} ve üzeri'
        else:
            ad = f'%{_tr(lo)} – %{_tr(hi)}'
        gruplar.append((ad, g, lo, hi))

    enb = max(len(g) for _, g, _, _ in gruplar) or 1

    print(f'\n>> GETİRİ ARALIKLARINA GÖRE DAĞILIM — {period}  ({n} hisse)\n')
    print(f'   {"Aralık":<22}{"Adet":>6}{"Pay":>8}{"Kümülatif":>11}{"Medyan":>10}   Dağılım')
    print('   ' + '-' * 76)

    kum = 0
    for ad, g, lo, hi in gruplar:
        c = len(g)
        kum += c
        pay = c / n * 100
        med = f'%{_tr(statistics.median(g))}' if g else '—'
        bar = '█' * round(c / enb * 26)
        satir = (f'   {ad:<22}{c:>6}{pay:>7.1f}%{kum / n * 100:>10.1f}%'
                 f'{med:>10}   {bar}')
        # Endeksin kendi getirisi hangi banda düşüyor?
        if own is not None and lo <= own < hi:
            satir += f'  ← ENDEKS (%{_tr(own)})'
        print(satir)

    if own is not None:
        gecen = sum(1 for v in vals if v > own)
        print(f'\n   Endeksi (%{_tr(own)}) geçen: {gecen} / {n}  (%{gecen / n * 100:.1f})')


def print_quartiles(rows, own, period):
    """Hisseleri getiriye göre 4 eşit gruba böler ve her grubu özetler.

    rows: [(kod, ad, pct)] · own: endeksin kendi getirisi (None olabilir)
    """
    s = sorted(rows, key=lambda x: x[2], reverse=True)
    n = len(s)
    if n < 4:
        return
    b = [round(n * i / 4) for i in range(5)]
    etiket = ['1. çeyrek (en iyi)', '2. çeyrek', '3. çeyrek', '4. çeyrek (en kötü)']

    print(f'\n>> ÇEYREKLİKLER — {period}  ({n} hisse, getiriye göre büyükten küçüğe)\n')
    head = (f'   {"Çeyrek":<22}{"Adet":>5}{"En düşük":>12}{"En yüksek":>12}'
            f'{"ORTALAMA":>12}{"MEDYAN":>12}')
    if own is not None:
        head += f'{"Endeksi geçen":>15}'
    print(head)
    print('   ' + '-' * (len(head) - 3))

    for i in range(4):
        g = [p for _, _, p in s[b[i]:b[i + 1]]]
        if not g:
            continue
        line = (f'   {etiket[i]:<22}{len(g):>5}{min(g):>12.1f}{max(g):>12.1f}'
                f'{statistics.mean(g):>12.1f}{statistics.median(g):>12.1f}')
        if own is not None:
            line += f'{sum(1 for x in g if x > own):>15}'
        print(line)

    if own is not None:
        toplam = sum(1 for _, _, p in s if p > own)
        print(f'\n   Endeksi (% {own:.1f}) geçen toplam: {toplam} / {n}  '
              f'(%{toplam / n * 100:.1f})')
    print('\n   Not: Çeyrek sınırları hisse SAYISINA göre eşit bölünür; ilk çeyrek')
    print('   içindeki dağılım çok geniş olabilir (birkaç uç değer ortalamayı şişirir,')
    print('   o yüzden her çeyrekte de MEDYAN daha temsil edicidir).')


def main():
    ap = argparse.ArgumentParser(description='Endeks bileşenleri getiri istatistiği')
    ap.add_argument('--index', default='XTUMY', help='Ticker veya panel adı (varsayılan XTUMY)')
    ap.add_argument('--period', default='5y', help='1a,3a,6a,1y,3y,5y,10y (varsayılan 5y)')
    ap.add_argument('--top', type=int, default=10, help='Kaç en iyi/en kötü gösterilsin')
    ap.add_argument('--list', action='store_true', help='Mevcut endeksleri listele')
    ap.add_argument('--bands', default=','.join(str(x) for x in DEFAULT_BANDS),
                    help='Getiri aralığı sınırları, virgülle. Örn: 0,100,500,1000,5000')
    ap.add_argument('--no-quartiles', action='store_true',
                    help='Eşit sayılı çeyreklik tablosunu gizle')
    a = ap.parse_args()

    if not DB_PATH.exists():
        sys.exit(f'HATA: {DB_PATH} yok. Önce: python collect_bist.py')

    conn = sqlite3.connect(DB_PATH)

    if a.list:
        for (n,) in conn.execute(
                'SELECT DISTINCT index_name FROM stock_returns ORDER BY index_name'):
            print(f'  {ENDEKSLER.get(n, "?"):<8} {n}')
        return

    idx = resolve_index(conn, a.index)
    cur = load_period(conn, idx, a.period)

    # Kırpılmış dönem tespiti için referans dilim (yalnızca haftalık dönemlerde anlamlı)
    ref = {}
    if a.period in ('3y', '5y') and a.period != FULL_HISTORY_REF:
        try:
            ref = load_period(conn, idx, FULL_HISTORY_REF)
        except SystemExit:
            ref = {}

    tam, kisa, veri_yok = [], [], []
    for kod, s in cur.items():
        pct = s.get('pct')
        if pct is None:
            veri_yok.append(kod)
            continue
        r = ref.get(kod, {}).get('pct') if ref else None
        # pct == referans → bar sayısı yetmemiş, dilim kırpılmış (tam geçmiş yok)
        if r is not None and abs(pct - r) < 1e-9:
            kisa.append((kod, s['ad'], pct))
        else:
            tam.append((kod, s['ad'], pct))

    ticker = ENDEKSLER.get(idx, '?')
    print('=' * 60)
    print(f'  {idx}  ({ticker})   ·   dönem: {a.period}')
    print('=' * 60)
    print(f'Endeksteki hisse   : {len(cur)}')
    print(f'Getirisi olan      : {len(tam) + len(kisa)}')
    if veri_yok:
        print(f'Veri yok           : {len(veri_yok)}  ({", ".join(veri_yok[:8])}'
              f'{"..." if len(veri_yok) > 8 else ""})')

    own = index_own_return(conn, idx, a.period)
    if own is not None:
        print(f'\nEndeksin kendi getirisi (piyasa değeri ağırlıklı): % {own:.2f}')

    hepsi = [p for _, _, p in tam + kisa]
    describe(hepsi, f'>> TÜM HİSSELER (ham — kısa geçmişliler dahil)')

    if kisa:
        print(f'\n  ! {len(kisa)} hisse {a.period} boyunca borsada değildi; onların '
              f'getirisi\n    daha kısa bir dönemi ölçüyor ve ortalamayı bozuyor.')
        sonuc = describe([p for _, _, p in tam],
                         f'>> TAM {a.period} GEÇMİŞİ OLANLAR (asıl bakılması gereken)')
    else:
        sonuc = None

    ana = tam if kisa else (tam + kisa)
    if own is not None and ana:
        med = statistics.median([p for _, _, p in ana])
        fark = med - own
        print(f'\n  Medyan hisse, endeksin kendisine göre: % {fark:+.2f} puan '
              f'({"daha iyi" if fark > 0 else "daha kötü"})')
        print('  (Endeks piyasa değeri ağırlıklı; medyan ise "ortadaki hisse".')
        print('   Medyan endeksin altındaysa, yükselişi birkaç büyük hisse taşımış demektir.)')

    if ana:
        try:
            edges = sorted(float(x) for x in a.bands.split(',') if x.strip() != '')
        except ValueError:
            sys.exit('HATA: --bands sayı listesi olmalı, ör: 0,100,500,1000')
        print_bands(ana, own, a.period, edges)
        if not a.no_quartiles:
            print_quartiles(ana, own, a.period)

    if a.top and ana:
        s = sorted(ana, key=lambda x: x[2], reverse=True)
        k = min(a.top, len(s) // 2)          # iki liste çakışmasın
        if k:
            print(f'\n>> EN İYİ {k}')
            for kod, ad, p in s[:k]:
                print(f'   {kod:<8} % {p:>9.2f}   {ad[:34]}')
            print(f'\n>> EN KÖTÜ {k}')
            for kod, ad, p in s[-k:][::-1]:
                print(f'   {kod:<8} % {p:>9.2f}   {ad[:34]}')

    print('\n' + '-' * 60)
    print('NOT: Liste endeksin BUGÜNKÜ bileşimidir. Dönem içinde borsadan çıkan veya')
    print('     endeksten düşen hisseler yok → "hayatta kalan yanlılığı" (survivorship')
    print('     bias) sonucu bir miktar yukarı çeker. Getiriler temettüsüz fiyat')
    print('     getirisidir ve enflasyona göre düzeltilmemiştir.')
    conn.close()


if __name__ == '__main__':
    main()
