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

PORTFOLIO_FILE = BASE_DIR / 'data' / 'portfolio.json'

# TradingView sembolleri — benchmark
BENCHMARK_TV = {
    'xu100': 'BIST:XU100',
    'altin': 'BIST:ALTIN',   # Gram Altın TL
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


# ── NSP fiyatı — TEFAS ───────────────────────────────────────────────────────
def _fetch_nsp(from_str: str) -> dict[str, float]:
    """TEFAS'tan NSP fiyatlarını çek. {date_str: price}"""
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
            if row.get('KOD') == 'NSP':
                try:
                    d = dt.strptime(row['TARIH'], '%d.%m.%Y').strftime('%Y-%m-%d')
                    prices[d] = float(row['FIYAT'])
                except Exception:
                    pass
        log.info('NSP: %d fiyat alındı', len(prices))
    except Exception as e:
        log.warning('NSP fetch hatası: %s', e)
    return prices


# ── Ana Güncelleme ────────────────────────────────────────────────────────────
def run():
    log.info('=== Portföy güncelleme başlıyor ===')

    pf = json.loads(PORTFOLIO_FILE.read_text(encoding='utf-8'))

    # Açık pozisyonlar
    open_pos = pf.get('open_positions', {})
    if not open_pos:
        log.info('Açık pozisyon yok, çıkılıyor.')
        return

    tickers = list(open_pos.keys())
    qty_map = {t: open_pos[t]['qty'] for t in tickers}
    log.info('Hisseler: %s', tickers)

    # Son portföy tarihi
    pdv = pf.get('portfolio_daily_value', [])
    if not pdv:
        log.error('portfolio_daily_value boş!')
        return

    last_entry    = pdv[-1]
    last_date_str = last_entry['date']
    last_date     = date.fromisoformat(last_date_str)
    last_cash     = last_entry.get('cash_value', 0.0)
    today         = date.today()

    if last_date >= today:
        log.info('Portföy zaten güncel (%s)', last_date_str)
        return

    n_bars = max(30, (today - last_date).days + 10)
    log.info('Son tarih: %s → bugün: %s (%d bar istenecek)', last_date_str, today, n_bars)

    # 1. Hisse fiyatları (TradingView)
    stock_prices: dict[str, dict[str, float]] = {}
    for ticker in tickers:
        log.info('Çekiliyor: %s', ticker)
        try:
            stock_prices[ticker] = _fetch_tv(f'BIST:{ticker}', n_bars)
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

    # 3. NSP fiyatları (TEFAS)
    nsp_prices   = _fetch_nsp(last_date_str)
    nsp_units    = pf.get('nsp_current_units', 0.0)
    last_nsp_val = last_entry.get('nsp_value', 0.0)

    # 4. Trading günleri → XU100'ün döndürdüğü tarihleri kullan
    xu100_dates = sorted(
        d for d in bench_raw.get('xu100', {})
        if d > last_date_str
    )
    log.info('Güncellenecek %d işlem günü', len(xu100_dates))

    # 5. Her yeni gün için portfolio_daily_value girdisi
    new_entries = []
    last_stock_prices: dict[str, float] = {t: list(v.values())[-1] for t, v in stock_prices.items() if v}

    for d_str in xu100_dates:
        # Mevcut fiyatı al (yoksa son bilinen fiyatı kullan)
        sv = 0.0
        for ticker, qty in qty_map.items():
            p = stock_prices[ticker].get(d_str)
            if p is not None:
                last_stock_prices[ticker] = p
            sv += qty * last_stock_prices.get(ticker, 0.0)

        # NSP değeri
        nsp_p = nsp_prices.get(d_str)
        nsp_val = (nsp_units * nsp_p) if nsp_p else last_nsp_val
        last_nsp_val = nsp_val

        total = round(sv + nsp_val + last_cash, 2)
        new_entries.append({
            'date':         d_str,
            'stock_value':  round(sv, 2),
            'nsp_value':    round(nsp_val, 2),
            'cash_value':   round(last_cash, 2),
            'total_value':  total,
        })

    # 6. Güncelle
    if new_entries:
        pf['portfolio_daily_value']  = pdv + new_entries
        pf['portfolio_current_value'] = new_entries[-1]['total_value']
        log.info('%d yeni gün eklendi. Son değer: %.2f TL', len(new_entries), new_entries[-1]['total_value'])

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

    # 9. nsp_daily_value'ye yeni NSP girdileri ekle
    nsp_dv = pf.get('nsp_daily_value', [])
    existing_nsp_dates = {e['date'] for e in nsp_dv}
    for d_str, nsp_p in sorted(nsp_prices.items()):
        if d_str > last_date_str and d_str not in existing_nsp_dates:
            nsp_dv.append({
                'date':  d_str,
                'units': nsp_units,
                'price': nsp_p,
                'value': round(nsp_units * nsp_p, 2),
            })
    pf['nsp_daily_value'] = sorted(nsp_dv, key=lambda x: x['date'])

    # 10. Kaydet
    PORTFOLIO_FILE.write_text(json.dumps(pf, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    log.info('=== portfolio.json güncellendi ===')


if __name__ == '__main__':
    run()
