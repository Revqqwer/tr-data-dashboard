"""
Portföy Günlük Güncelleme
Açık pozisyonları portfolio.json'dan dinamik okur,
TradingView WebSocket ile fiyat çeker,
portfolio_daily_value / last_prices / benchmark_prices günceller.

Kullanım:
    cd ~/tr-data-dashboard
    python3.10 update_portfolio.py
"""
import json, logging, os, sys, time
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / '.env', override=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

PORTFOLIO_FILE           = BASE_DIR / 'data' / 'portfolio.json'
PORTFOLIO_OVERRIDES_FILE = BASE_DIR / 'data' / 'portfolio_overrides.json'

# TradingView sembolleri — benchmark
BENCHMARK_TV = {
    'xu100': 'BIST:XU100',
    'altin': 'FX_IDC:XAUTRYG',   # Gram Altın TL (TradingView FX_IDC:XAUTRYG)
}

# parse_portfolio.py ile aynı harita — Yahoo Finance'da olmayan ticker'lar için TV sembolü
TV_FALLBACK = {
    'DMLKTG': 'DMLKT',
    'ALTINS': 'ALTIN',
}


# ── TradingView fetch (collect_bist.py'den — timeout'lu, güvenilir) ──────────
def _fetch_tv(tv_symbol: str, n_bars: int = 60) -> dict[str, float]:
    """TradingView WebSocket'ten günlük kapanış çek. {date_str: close}
    collect_bist.py'daki fetch_tv_ws'i kullanır (max 40 deneme, hızlı çıkış).
    """
    # Exchange ve symbol'ü ayır: "BIST:AKBNK" → exchange=BIST, symbol=AKBNK
    if ':' in tv_symbol:
        exchange, symbol = tv_symbol.split(':', 1)
    else:
        exchange, symbol = 'BIST', tv_symbol

    # collect_bist.py'deki fonksiyonu doğrudan çağır
    import sys
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    # collect_bist modülündeki fetch_tv_ws'i import et
    import importlib.util
    spec = importlib.util.spec_from_file_location("collect_bist", BASE_DIR / "collect_bist.py")
    cb   = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)

    dates, closes = cb.fetch_tv_ws(symbol, exchange, n_bars)
    if not dates:
        return {}
    return dict(zip(dates, closes))


# ── Para piyasası fonu fiyatı — TEFAS ────────────────────────────────────────
# NSP + GOP burada aynı mantıkla çekilir. Fon fiyat bulunamazsa (ör. GOP bu
# uçtan hiç dönmüyor) çağıran taraf son bilinen fiyatı taşımalı — burada
# sessizce boş dict dönülür, hata sayılmaz.
def _fetch_mmf(fund_code: str, from_str: str) -> dict[str, float]:
    """TEFAS'tan bir para piyasası fonunun fiyatlarını çek. {date_str: price}"""
    import requests
    from datetime import datetime as dt
    prices = {}
    try:
        start = dt.strptime(from_str, '%Y-%m-%d').strftime('%d.%m.%Y')
        end   = date.today().strftime('%d.%m.%Y')
        r = requests.post(
            'https://www.tefas.gov.tr/api/DB/BindHistoryInfo',
            json={'fontip': 'YAT', 'bastarih': start, 'bittarih': end},
            headers={'Content-Type': 'application/json'},
            timeout=30,
        )
        for row in r.json().get('data', []):
            if row.get('KOD') == fund_code:
                try:
                    d = dt.strptime(row['TARIH'], '%d.%m.%Y').strftime('%Y-%m-%d')
                    prices[d] = float(row['FIYAT'])
                except Exception:
                    pass
        log.info('%s: %d fiyat alındı', fund_code, len(prices))
    except Exception as e:
        log.warning('%s fetch hatası: %s', fund_code, e)
    return prices


def _fetch_nsp(from_str: str) -> dict[str, float]:
    return _fetch_mmf('NSP', from_str)


# ── Ana Güncelleme ────────────────────────────────────────────────────────────
def run():
    log.info('=== Portföy güncelleme başlıyor ===')

    today = date.today()
    pf = json.loads(PORTFOLIO_FILE.read_text(encoding='utf-8'))

    # Açık pozisyonlar (portfolio.json + portfolio_overrides.json birleşimi)
    base_open = dict(pf.get('open_positions', {}))
    open_pos  = dict(base_open)
    ov        = {}
    try:
        ov = json.loads(PORTFOLIO_OVERRIDES_FILE.read_text(encoding='utf-8'))
        for ticker, pos_data in ov.get('open_positions', {}).items():
            if float(pos_data.get('qty', 0)) <= 0:
                open_pos.pop(ticker, None)
            else:
                open_pos[ticker] = pos_data
        if ov.get('open_positions'):
            log.info('Override pozisyonları uygulandı: %s', list(ov['open_positions'].keys()))
    except FileNotFoundError:
        pass

    # Yeni pozisyonlar (sadece override'da, base portfolio.json'da yok):
    # purchased_date'den önce sayılmaz — geçmiş giriş şişirmesini önler.
    # purchased_date yoksa bugün varsayılır.
    new_pos_dates: dict[str, str] = {}
    for ticker, pos_data in ov.get('open_positions', {}).items():
        if ticker not in base_open and float(pos_data.get('qty', 0)) > 0:
            pd_str = pos_data.get('purchased_date', today.isoformat())
            new_pos_dates[ticker] = pd_str
            log.info('Yeni override pozisyon: %s → alım tarihi %s', ticker, pd_str)

    # ── Fonlama: NSP + nakit override'ı, yeni alımların KENDİ tarihlerine
    #    maliyetlerine orantılı dağıtılır. Böylece her hisse eklendiği gün
    #    karşılığı fon düşülür → grafikte sahte sıçrama olmaz ve seri tam
    #    doğru son değerde (override) biter. ──
    nsp_units_base     = pf.get('nsp_current_units', 0.0)
    nsp_units_override = ov.get('nsp_units_override')
    gop_units_base     = pf.get('gop_current_units', 0.0)
    gop_units_override = ov.get('gop_units_override')
    cash_override      = ov.get('cash_value_override')

    new_pos_cost = {
        t: float(ov['open_positions'][t].get('qty', 0)) * float(ov['open_positions'][t].get('avg_cost', 0))
        for t in new_pos_dates
    }
    total_new_cost = sum(new_pos_cost.values()) or 1.0   # 0'a bölmeyi önle

    total_nsp_reduction = (nsp_units_base - float(nsp_units_override)) if nsp_units_override is not None else 0.0
    # Her pozisyonun alım tarihinde düşülecek NSP birimi (maliyetine orantılı)
    nsp_units_removed = {t: total_nsp_reduction * (new_pos_cost[t] / total_new_cost) for t in new_pos_dates}

    if not open_pos:
        log.info('Açık pozisyon yok, çıkılıyor.')
        return

    # Son portföy tarihi
    pdv = pf.get('portfolio_daily_value', [])
    if not pdv:
        log.error('portfolio_daily_value boş!')
        return

    last_entry    = pdv[-1]
    last_date_str = last_entry['date']
    last_date     = date.fromisoformat(last_date_str)

    if last_date >= today:
        log.info('Portföy zaten güncel (%s)', last_date_str)
        return

    # Son N işlem gününü her çalışmada yeniden hesapla — yanlış fiyatla
    # yazılmış girişleri (ör. TV sembol hatası, yeni pozisyon ekleme) düzeltir.
    RECOMPUTE_DAYS = 20
    recompute_from = last_date - timedelta(days=RECOMPUTE_DAYS)
    recompute_from_str = recompute_from.isoformat()

    # ── GERÇEK işlem geçmişinden (pf['trades']) tarihe göre kademe fonksiyonları ──
    # Önceki hâl: 'qty_map' (BUGÜNÜN nihai adetleri) ve nakit, sadece admin panel
    # override'ı ('new_pos_dates') varsa tarihe duyarlıydı — pencere İÇİNDE
    # gerçekleşen ORGANİK alım/satımlar (ör. FRMPL alımı, ALBRK/QUICK/BRSAN
    # satışı, GOP alımları) hem hisse değerine hem nakde hiç yansımıyordu; nakit
    # RECOMPUTE_DAYS öncesindeki (anchor) değerde donuk kalıyor, o günlerde henüz
    # alınmamış/satılmış hisseler de yanlış (hep bugünkü nihai adetle) hesaplanıyordu.
    real_trades = sorted(pf.get('trades', []), key=lambda t: t['date'])
    qty_steps: dict[str, list] = {}
    _running_qty: dict[str, float] = {}
    trade_cash_steps: list = []      # [(date, kumulatif_gercek_hisse+fon_nakit_akisi)]
    _running_trade_cash = 0.0
    mmf_all_trades = list(pf.get('nsp_trades', [])) + list(pf.get('gop_trades', []))
    for t in sorted(real_trades + mmf_all_trades, key=lambda x: x['date']):
        if 'ticker' in t:   # hisse işlemi
            tk = t['ticker']
            _running_qty[tk] = _running_qty.get(tk, 0.0) + (t['qty'] if t['type'] == 'alis' else -t['qty'])
            qty_steps.setdefault(tk, []).append((t['date'], _running_qty[tk]))
            amount = t['amount']
        else:                # NSP/GOP fon işlemi
            amount = t['units'] * t['price']
        _running_trade_cash += (amount if t['type'] == 'satis' else -amount)
        trade_cash_steps.append((t['date'], _running_trade_cash))

    def _qty_at(ticker: str, d_str: str) -> float:
        steps = qty_steps.get(ticker)
        if not steps:
            return float(open_pos.get(ticker, {}).get('qty', 0))  # override-only pozisyon (gerçek işlemi yok)
        q = 0.0
        for sd, sq in steps:
            if sd <= d_str:
                q = sq
            else:
                break
        return q

    def _trade_cash_delta_since(anchor_str: str, d_str: str) -> float:
        """anchor_str (hariç) ile d_str (dahil) arasında GERÇEK hisse+fon işlemlerinden net nakit değişimi."""
        a = b = 0.0
        for sd, cum in trade_cash_steps:
            if sd <= anchor_str:
                a = cum
            if sd <= d_str:
                b = cum
        return b - a

    # Fiyat çekilecek tickerlar: bugün açık olanlar + pencere içinde işlem görüp
    # kapanmış olanlar (ör. ALBRK, QUICK) — yoksa o günlerin gerçek hisse değeri
    # hesaplanamaz (fiyatı hiç çekilmemiş olur).
    window_tickers = {t['ticker'] for t in real_trades if t['date'] > recompute_from_str}
    tickers = sorted(set(open_pos.keys()) | window_tickers)
    log.info('Hisseler (fiyat çekilecek): %s', tickers)

    n_bars = max(30, (today - recompute_from).days + 10)
    log.info('Son tarih: %s → bugün: %s (%d bar istenecek, son %d gün yeniden hesaplanacak)',
             last_date_str, today, n_bars, RECOMPUTE_DAYS)

    # 1. Hisse fiyatları (TradingView)
    stock_prices: dict[str, dict[str, float]] = {}
    for ticker in tickers:
        tv_sym = f'BIST:{TV_FALLBACK.get(ticker, ticker)}'
        log.info('Çekiliyor: %s (%s)', ticker, tv_sym)
        try:
            stock_prices[ticker] = _fetch_tv(tv_sym, n_bars)
        except Exception as e:
            log.warning('%s hatası: %s', ticker, e)
            stock_prices[ticker] = {}
        time.sleep(0.6)

    # 2. Benchmark (XU100, Altın)
    bench_raw: dict[str, dict[str, float]] = {}
    for key, tv_sym in BENCHMARK_TV.items():
        log.info('Benchmark çekiliyor: %s', key)
        try:
            bench_raw[key] = _fetch_tv(tv_sym, n_bars)
        except Exception as e:
            log.warning('Benchmark %s hatası: %s', key, e)
            bench_raw[key] = {}
        time.sleep(0.6)

    # 3. NSP + GOP fiyatları (TEFAS). GOP fonu yoksa (henüz alınmadıysa) hiç çekilmez.
    nsp_prices   = _fetch_nsp(recompute_from_str)
    gop_prices   = _fetch_mmf('GOP', recompute_from_str) if (gop_units_base or gop_units_override) else {}
    # Yeniden hesaplama başlangıç noktasındaki değeri bul
    # NOT: 'nsp_value' alanı artık NSP+GOP TOPLAMINI tutuyor (parse_portfolio.py ile aynı
    # kural) — bu yüzden aşağıdaki NSP fallback'i aslında anchor'daki havuz toplamıdır.
    pdv_kept_tmp = [e for e in pdv if e['date'] <= recompute_from_str]
    recompute_anchor = pdv_kept_tmp[-1] if pdv_kept_tmp else last_entry
    last_nsp_val = recompute_anchor.get('nsp_value', 0.0)
    last_cash    = recompute_anchor.get('cash_value', 0.0)
    # Nakit override'ı da yeni alımlara orantılı dağıt.
    # Anchor'da zaten başlamış alımların payını geri çıkar → run'lar arası çift sayım olmaz.
    frac_anchor = sum(new_pos_cost.get(t, 0.0) for t in new_pos_dates
                      if new_pos_dates[t] <= recompute_from_str) / total_new_cost
    cash_target = float(cash_override) if cash_override is not None else last_cash
    _denom      = 1.0 - frac_anchor
    cash_delta  = (cash_target - last_cash) / _denom if _denom > 1e-6 else 0.0
    cash_base_abs = last_cash - cash_delta * frac_anchor
    cash_added  = {t: cash_delta * (new_pos_cost.get(t, 0.0) / total_new_cost) for t in new_pos_dates}
    # TEFAS fiyat gelmediğinde birim değişikliğini yansıtmak için son bilinen NSP fiyatı
    nsp_dv_all = pf.get('nsp_daily_value', [])
    last_nsp_price_known = nsp_dv_all[-1]['price'] if nsp_dv_all else None
    if last_nsp_price_known is None and last_nsp_val and nsp_units_base:
        last_nsp_price_known = last_nsp_val / nsp_units_base

    # Aynısı GOP için — kendi (ayrı) fiyat serisinden son bilinen fiyat
    gop_dv_all = pf.get('gop_daily_value', [])
    last_gop_price_known = gop_dv_all[-1]['price'] if gop_dv_all else None
    gop_u0 = float(gop_units_override) if gop_units_override is not None else float(gop_units_base)
    last_gop_val = gop_u0 * last_gop_price_known if last_gop_price_known else 0.0

    # GOP birim sayısı zaman içinde değişmiş olabilir (ör. birden fazla alım
    # farklı tarihlerde) — RECOMPUTE_DAYS penceresi bu alımların tümünü
    # kapsayabilir. gop_position_history'den (gerçek işlem geçmişi) tarihe göre
    # kademe fonksiyonu kurup HER GÜN o günkü GERÇEK birim sayısını kullan;
    # yoksa geçmiş günlere bugünün (daha büyük) birim sayısı yanlışlıkla
    # uygulanıp grafikte sahte bir sıçrama oluşur (nitekim oluşmuştu).
    _gop_steps = sorted(
        (e['date'], float(e['units'])) for e in pf.get('gop_position_history', [])
    )

    def _gop_units_at(d_str: str) -> float:
        if gop_units_override is not None:
            return float(gop_units_override)
        if not _gop_steps:
            return gop_u0
        u = 0.0
        for sd, su in _gop_steps:
            if sd <= d_str:
                u = su
            else:
                break
        return u

    # 4. Trading günleri → XU100'ün döndürdüğü tarihleri kullan
    # recompute_from_str'den itibaren yeniden hesapla (son RECOMPUTE_DAYS günü dahil)
    xu100_dates = sorted(
        d for d in bench_raw.get('xu100', {})
        if d > recompute_from_str
    )
    log.info('Güncellenecek/yeniden hesaplanacak %d işlem günü (%s sonrası)',
             len(xu100_dates), recompute_from_str)

    # Yeni pozisyon override'ı yoksa nakit override'ını günlere göre lineer RAMP ile
    # uygula (aksi halde cash_added boş kalır → override uygulanmaz, nakit anchor'da takılır)
    cash_ramp = None
    if cash_override is not None and not new_pos_dates and xu100_dates:
        _n = len(xu100_dates)
        cash_ramp = {ds: last_cash + (cash_target - last_cash) * (i + 1) / _n
                     for i, ds in enumerate(xu100_dates)}

    # 5. Her yeni gün için portfolio_daily_value girdisi
    new_entries = []
    # Mevcut last_prices'ı başlangıç noktası yap — TV fetch başarısız olan ticker'lar
    # (örn. DMLKTG → TV'de DMLKT olarak var) için son bilinen fiyatı koru.
    last_stock_prices: dict[str, float] = dict(pf.get('last_prices', {}))
    for t, v in stock_prices.items():
        if v:
            last_stock_prices[t] = list(v.values())[-1]

    for d_str in xu100_dates:
        # Hisse değeri: o GÜNE ait GERÇEK adet (qty_steps'ten) × o günün fiyatı.
        # Override-only pozisyonlar (gerçek işlemi olmayan, admin panelinden
        # eklenenler) hâlâ new_pos_dates ile alım tarihinden önce hariç tutulur.
        sv = 0.0
        for ticker in tickers:
            if ticker in new_pos_dates and d_str < new_pos_dates[ticker]:
                continue
            qty = _qty_at(ticker, d_str)
            if qty <= 0:
                continue
            p = stock_prices.get(ticker, {}).get(d_str)
            if p is not None:
                last_stock_prices[ticker] = p
            sv += qty * last_stock_prices.get(ticker, 0.0)

        # O güne kadar alımı yapılmış yeni (override) pozisyonlar → fonlamayı kademeli düş
        started = [t for t in new_pos_dates if d_str >= new_pos_dates[t]]
        nsp_u  = nsp_units_base - sum(nsp_units_removed.get(t, 0.0) for t in started)
        if cash_ramp is not None:
            cash_d = cash_ramp[d_str]
        else:
            # GERÇEK işlemlerin (hisse + NSP/GOP alım-satım) bu pencerede yarattığı
            # net nakit değişimi + (varsa) override fonlama ayarı.
            cash_d = (cash_base_abs
                      + _trade_cash_delta_since(recompute_from_str, d_str)
                      + sum(cash_added.get(t, 0.0) for t in started))
        nsp_p = nsp_prices.get(d_str)
        if nsp_p:
            last_nsp_price_known = nsp_p
            nsp_val = nsp_u * nsp_p
        elif last_nsp_price_known:
            # TEFAS fiyat vermediğinde son bilinen fiyatla birimi uygula
            nsp_val = nsp_u * last_nsp_price_known
        else:
            nsp_val = last_nsp_val

        # GOP — aynı mantık, ayrı fon; birim sayısı o GÜNE ait gerçek değer
        # (henüz alınmadıysa 0, val=0)
        gop_u = _gop_units_at(d_str)
        gop_p = gop_prices.get(d_str)
        if gop_p:
            last_gop_price_known = gop_p
            gop_val = gop_u * gop_p
        elif last_gop_price_known:
            gop_val = gop_u * last_gop_price_known
        else:
            gop_val = last_gop_val
        last_gop_val = gop_val
        last_nsp_val = nsp_val

        # 'nsp_value' alanı NSP+GOP toplamını tutar (parse_portfolio.py ile aynı kural;
        # GOP'u ayrıca eklemeyi unutmak, GOP'un tüm değerini toplamdan düşürüyordu).
        total = round(sv + nsp_val + gop_val + cash_d, 2)
        new_entries.append({
            'date':         d_str,
            'stock_value':  round(sv, 2),
            'nsp_value':    round(nsp_val + gop_val, 2),
            'cash_value':   round(cash_d, 2),
            'total_value':  total,
        })

    # 6. Güncelle: recompute_from_str öncesini koru, sonrasını yenisiyle değiştir
    if new_entries:
        pdv_kept = [e for e in pdv if e['date'] <= recompute_from_str]
        pf['portfolio_daily_value']   = pdv_kept + new_entries
        pf['portfolio_current_value'] = new_entries[-1]['total_value']
        log.info('%d gün yeniden hesaplandı (%d yeni, %d güncellendi). Son değer: %.2f TL',
                 len(new_entries),
                 sum(1 for e in new_entries if e['date'] > last_date_str),
                 sum(1 for e in new_entries if e['date'] <= last_date_str),
                 new_entries[-1]['total_value'])

    # 7. last_prices güncelle
    pf['last_prices'] = {t: v for t, v in last_stock_prices.items() if v}

    # 8. benchmark_prices güncelle (dict formatı: {date_str: float})
    existing_bench = pf.get('benchmark_prices', {})
    for key, new_data in bench_raw.items():
        existing = existing_bench.get(key, {})
        if isinstance(existing, list):
            existing = {item[0]: item[1] for item in existing}
        existing.update(new_data)
        existing_bench[key] = dict(sorted(existing.items()))
    pf['benchmark_prices'] = existing_bench

    # 9. nsp_daily_value / gop_daily_value'ye yeni girdiler ekle
    nsp_dv = pf.get('nsp_daily_value', [])
    existing_nsp_dates = {e['date'] for e in nsp_dv}
    for d_str, nsp_p in sorted(nsp_prices.items()):
        if d_str > last_date_str and d_str not in existing_nsp_dates:
            u = (nsp_units_override if nsp_units_override is not None else nsp_units_base)
            nsp_dv.append({
                'date':  d_str,
                'units': u,
                'price': nsp_p,
                'value': round(u * nsp_p, 2),
            })
    pf['nsp_daily_value'] = sorted(nsp_dv, key=lambda x: x['date'])

    gop_dv = pf.get('gop_daily_value', [])
    existing_gop_dates = {e['date'] for e in gop_dv}
    for d_str, gop_p in sorted(gop_prices.items()):
        if d_str > last_date_str and d_str not in existing_gop_dates:
            u = _gop_units_at(d_str)
            gop_dv.append({
                'date':  d_str,
                'units': u,
                'price': gop_p,
                'value': round(u * gop_p, 2),
            })
    pf['gop_daily_value'] = sorted(gop_dv, key=lambda x: x['date'])

    # 10. Kaydet
    PORTFOLIO_FILE.write_text(json.dumps(pf, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    log.info('=== portfolio.json güncellendi ===')


if __name__ == '__main__':
    run()
