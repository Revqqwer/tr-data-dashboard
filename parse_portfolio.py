#!/usr/bin/env python3
"""Parse broker account statement (Ekstre3 (1).pdf) → data/portfolio.json"""
import pdfplumber
import re
import json
import os
import time
import requests
from datetime import datetime, date
from collections import defaultdict

PDF_PATH  = os.path.join(os.path.dirname(__file__), '..', '..', 'Downloads', 'Ekstre3 (1).pdf')
OUT_PATH  = os.path.join(os.path.dirname(__file__), 'data', 'portfolio.json')

# GENKMH is a rights-derived lot, treat as same stock GENKM
TICKER_NORM = {'GENKMH': 'GENKM'}


def tr_float(s: str) -> float:
    return float(s.strip().replace('.', '').replace(',', '.'))


def infer_year(month: int, row_date: date) -> int:
    y = row_date.year
    if row_date.month <= 2 and month >= 11:
        y -= 1
    elif row_date.month >= 11 and month <= 2:
        y += 1
    return y


def extract_lines(pdf_path: str):
    """Extract raw transaction rows from all pages."""
    line_re    = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+1200\.100\s+(.+)$')
    amounts_re = re.compile(r'(-?[\d\.]+,\d{2})\s+(-?[\d\.]+,\d{2})\s+(-?[\d\.]+,\d{2})\s*$')
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            for line in text.split('\n'):
                m = line_re.match(line.strip())
                if not m:
                    continue
                rest = m.group(2)
                m2 = amounts_re.search(rest)
                if not m2:
                    continue
                rows.append({
                    'row_date':    datetime.strptime(m.group(1), '%d/%m/%Y').date(),
                    'description': rest[:m2.start()].strip(),
                    'borc':        tr_float(m2.group(1)),
                    'alacak':      tr_float(m2.group(2)),
                    'bakiye':      tr_float(m2.group(3)),
                })
    return rows


STOCK_RE    = re.compile(r'(\d{2}/\d{2})\s+([A-Z][A-Z0-9]+)\s+([\d\.]+)x([\d,]+)\s+TL\s+(Ali[sş]?|Sati[sş]?|Sat[ıi][sş]?)', re.I)
RIGHTS_RE   = re.compile(r'([A-Z][A-Z0-9]+)\s+\d+\s+Talep\s+\d+x[\d,]+\s+-\s+kabul\s+(\d+)x([\d,]+)')
DIVIDEND_RE = re.compile(r'([A-Z][A-Z0-9]+)\s+(\d+)\s+Lot.*?Temett', re.I)
KOMIS_RE    = re.compile(r'KOMISYON|Borsa Pay|Islem Komisyonu|Saklama Komisyonu|Stopaj|Vergi|BSMV', re.I)
NSP_RE      = re.compile(r'NSP B Tipi\s+([\d\.]+)\s+Payx([\d,]+)\s+(Sat|Ali)', re.I)


def process(rows):
    trades        = []
    nsp_trades    = []
    dividends     = []
    balance_pts   = {}   # date -> last bakiye
    total_komis   = 0.0

    for r in rows:
        d    = r['row_date']
        desc = r['description']
        balance_pts[d.isoformat()] = r['bakiye']

        # NSP money-market fund — track separately
        m = NSP_RE.search(desc)
        if m:
            units = float(m.group(1).replace('.', ''))   # "58.075" → 58075 (thousands sep)
            price = tr_float(m.group(2))
            # Use borc/alacak to determine direction — Turkish char encoding is unreliable
            ttype = 'alis' if r['borc'] > 0 else 'satis'
            nsp_trades.append({
                'date':  d.isoformat(),
                'units': units,
                'price': price,
                'type':  ttype,
            })
            continue

        # Commission / fees
        if KOMIS_RE.search(desc):
            total_komis += r['borc']
            continue

        # Dividend
        m = DIVIDEND_RE.search(desc)
        if m and r['alacak'] > 0:
            ticker = TICKER_NORM.get(m.group(1), m.group(1))
            dividends.append({
                'date':   d.isoformat(),
                'ticker': ticker,
                'qty':    int(m.group(2)),
                'net':    r['alacak'],
            })
            continue

        # Rights issue settlement (Talep/kabul)
        m = RIGHTS_RE.search(desc)
        if m:
            ticker = TICKER_NORM.get(m.group(1), m.group(1))
            qty    = int(m.group(2))
            price  = tr_float(m.group(3))
            if qty > 0:
                trades.append({
                    'date':       d.isoformat(),
                    'settle_date': d.isoformat(),
                    'ticker':     ticker,
                    'qty':        qty,
                    'price':      price,
                    'type':       'alis',
                    'amount':     round(qty * price, 2),
                    'is_rights':  True,
                })
            continue

        # Regular stock trade
        m = STOCK_RE.search(desc)
        if m:
            dd_mm  = m.group(1)
            ticker = TICKER_NORM.get(m.group(2), m.group(2))
            qty    = int(m.group(3).replace('.', ''))
            price  = tr_float(m.group(4))
            ttype  = 'alis' if m.group(5).lower().startswith('ali') else 'satis'
            month  = int(dd_mm[3:5])
            year   = infer_year(month, d)
            tdate  = date(year, month, int(dd_mm[:2]))
            amount = r['alacak'] if ttype == 'satis' else r['borc']
            trades.append({
                'date':        tdate.isoformat(),
                'settle_date': d.isoformat(),
                'ticker':      ticker,
                'qty':         qty,
                'price':       price,
                'type':        ttype,
                'amount':      amount,
                'is_rights':   False,
            })

    # NSP position history: running units at each transaction date
    nsp_units = 0.0
    nsp_position_history = []
    for t in sorted(nsp_trades, key=lambda x: x['date']):
        nsp_units += t['units'] if t['type'] == 'alis' else -t['units']
        nsp_position_history.append({
            'date':  t['date'],
            'units': round(nsp_units, 3),
            'price': t['price'],
            'value': round(nsp_units * t['price'], 2),
        })

    # Daily balance (keep last value per day, sorted)
    balance_history = [
        {'date': k, 'balance': v}
        for k, v in sorted(balance_pts.items())
    ]

    return trades, nsp_trades, nsp_position_history, dividends, balance_history, total_komis


def compute_pnl(trades):
    """FIFO realized P&L per ticker + cumulative P&L timeline."""
    lots     = defaultdict(list)   # ticker -> [{qty, price}]
    summary  = {}
    timeline = []   # [{date, ticker, pnl, cumulative_pnl}]
    cum_pnl  = 0.0

    # Anchor point at day before first trade
    sorted_trades = sorted(trades, key=lambda x: x['date'])
    if sorted_trades:
        from datetime import datetime, timedelta
        first = datetime.strptime(sorted_trades[0]['date'], '%Y-%m-%d').date()
        anchor = (first - timedelta(days=1)).isoformat()
        timeline.append({'date': anchor, 'ticker': '', 'pnl': 0.0, 'cumulative_pnl': 0.0})

    for t in sorted_trades:
        ticker = t['ticker']
        if ticker not in summary:
            summary[ticker] = {
                'buy_qty': 0, 'buy_amount': 0.0,
                'sell_qty': 0, 'sell_amount': 0.0,
                'realized_pnl': 0.0,
            }
        s = summary[ticker]

        if t['type'] == 'alis':
            lots[ticker].append({'qty': t['qty'], 'price': t['price']})
            s['buy_qty']    += t['qty']
            s['buy_amount'] += t['amount']
        else:
            remaining    = t['qty']
            cost_of_sold = 0.0
            while remaining > 0 and lots[ticker]:
                lot  = lots[ticker][0]
                take = min(lot['qty'], remaining)
                cost_of_sold += take * lot['price']
                lot['qty']   -= take
                remaining    -= take
                if lot['qty'] == 0:
                    lots[ticker].pop(0)
            pnl = t['amount'] - cost_of_sold
            s['sell_qty']     += t['qty']
            s['sell_amount']  += t['amount']
            s['realized_pnl'] += pnl
            cum_pnl           += pnl
            timeline.append({
                'date':           t['settle_date'],
                'ticker':         ticker,
                'pnl':            round(pnl, 2),
                'cumulative_pnl': round(cum_pnl, 2),
            })

    # Open positions (remaining lots)
    open_positions = {}
    for ticker, remaining_lots in lots.items():
        total_qty = sum(l['qty'] for l in remaining_lots)
        if total_qty > 0:
            avg = sum(l['qty'] * l['price'] for l in remaining_lots) / total_qty
            open_positions[ticker] = {
                'qty':        total_qty,
                'avg_cost':   round(avg, 4),
                'cost_basis': round(total_qty * avg, 2),
            }

    return summary, open_positions, timeline


def fetch_nsp_prices(start_date: date, end_date: date) -> dict:
    """Fetch NSP daily prices from TEFAS API. Returns {date_str: price}.
    Uses 14-day chunks (API silently returns null for larger ranges).
    """
    from datetime import timedelta
    TEFAS_URL = 'https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetirDosya'
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.tefas.gov.tr/',
    }
    prices = {}
    chunk_start = start_date
    s = None
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get('https://www.tefas.gov.tr/', timeout=15)
        time.sleep(1)

        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=13), end_date)  # 14-day window
            payload = {
                'dil': 'TR', 'fonTipi': 'YAT', 'islem': 1,
                'basTarih': chunk_start.strftime('%Y%m%d'),
                'bitTarih': chunk_end.strftime('%Y%m%d'),
                'kurucuKodu': None, 'sfonTurKod': None,
                'fonTurAciklama': None, 'fonTurKod': None, 'fonGrubu': None,
                'donemGetiri1a': '1', 'donemGetiri3a': '1', 'donemGetiri6a': '1',
                'donemGetiri1y': '1', 'donemGetiriyb': '1',
                'donemGetiri3y': '1', 'donemGetiri5y': '1',
            }
            for attempt in range(3):
                try:
                    resp = s.post(TEFAS_URL, json=payload, timeout=30)
                    if not resp.text.strip():
                        raise ValueError('Empty response')
                    result_list = resp.json().get('resultList') or []
                    for row in result_list:
                        if row.get('fonKodu') == 'NSP' and row.get('fiyat'):
                            prices[row['tarih']] = float(row['fiyat'])
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f'  Chunk {chunk_start} failed after 3 tries: {e}')
                    else:
                        # Refresh session on failure
                        s = requests.Session()
                        s.headers.update(HEADERS)
                        s.get('https://www.tefas.gov.tr/', timeout=15)
                        time.sleep(2 + attempt * 2)

            chunk_start = chunk_end + timedelta(days=1)
            time.sleep(0.4)

    except Exception as e:
        print(f'TEFAS NSP fetch error: {e}')
    return prices


def build_nsp_daily_value(nsp_position_history: list, nsp_prices: dict) -> list:
    """
    For every date with a TEFAS price, find how many NSP units were held
    (step function from transaction history) and compute value = units * price.
    """
    if not nsp_position_history:
        return []

    # Build step-function: sorted list of (date_str, units)
    steps = sorted([(e['date'], e['units']) for e in nsp_position_history], key=lambda x: x[0])

    def units_at(d_str: str) -> float:
        """Return units held on date d_str using step function."""
        units = 0.0
        for step_date, step_units in steps:
            if step_date <= d_str:
                units = step_units
            else:
                break
        return units

    result = []
    for d_str in sorted(nsp_prices.keys()):
        u = units_at(d_str)
        if u > 0:
            p = nsp_prices[d_str]
            result.append({
                'date':  d_str,
                'units': round(u, 3),
                'price': round(p, 6),
                'value': round(u * p, 2),
            })
    return result


def _fetch_tv_ws(symbol: str, exchange: str = 'BIST', n_bars: int = 300) -> dict:
    """
    Fetch daily close prices from TradingView via WebSocket.
    Uses only websocket-client (pip install websocket-client) — no tvdatafeed needed.
    Returns {date_str: close_price}.
    """
    import websocket as _ws
    import random, string, re

    def _gen():
        return ''.join(random.choices(string.ascii_lowercase, k=12))

    def _wrap(msg):
        s = json.dumps(msg)
        return f'~m~{len(s)}~m~{s}'

    def _send(ws, func, args):
        ws.send(_wrap({'m': func, 'p': args}))

    def _packets(message):
        for m in re.finditer(r'~m~(\d+)~m~', message):
            length = int(m.group(1))
            yield message[m.end(): m.end() + length]

    chart_sess = 'cs_' + _gen()
    results: dict = {}

    def on_message(ws, message):
        for content in _packets(message):
            if not content.startswith('{'):
                continue
            try:
                obj = json.loads(content)
            except Exception:
                continue
            if obj.get('m') == 'timescale_update':
                p = obj.get('p', [])
                if len(p) >= 2 and isinstance(p[1], dict):
                    for series_val in p[1].values():
                        if isinstance(series_val, dict):
                            for bar in series_val.get('s', []):
                                v = bar.get('v', [])
                                if len(v) >= 5:
                                    from datetime import datetime as _dt, timezone as _tz
                                    d = _dt.fromtimestamp(int(v[0]), tz=_tz.utc).date()
                                    results[str(d)] = round(float(v[4]), 4)
                ws.close()

    def on_open(ws):
        _send(ws, 'set_auth_token', ['unauthorized_user_token'])
        _send(ws, 'chart_create_session', [chart_sess, ''])
        sym_json = json.dumps({'symbol': f'{exchange}:{symbol}', 'adjustment': 'splits'})
        _send(ws, 'resolve_symbol', [chart_sess, 'sds_sym_1', f'={sym_json}'])
        _send(ws, 'create_series', [chart_sess, 's1', 's1', 'sds_sym_1', 'D', n_bars, ''])

    app = _ws.WebSocketApp(
        'wss://data.tradingview.com/socket.io/websocket',
        header={'Origin': 'https://data.tradingview.com', 'User-Agent': 'Mozilla/5.0'},
        on_message=on_message,
        on_open=on_open,
    )
    app.run_forever(ping_interval=0)
    return results


def fetch_stock_prices(tickers: list, start_date: date, end_date: date) -> dict:
    """
    Fetch daily closing prices for BIST tickers.
    Primary: yfinance (.IS suffix).
    Fallback for instruments not on Yahoo Finance: tvdatafeed (TradingView).
    Returns {ticker: {date_str: price}}.
    """
    from datetime import timedelta

    # Tickers not on Yahoo Finance → map to tvdatafeed (TradingView) symbol
    TV_FALLBACK = {
        'DMLKTG': 'DMLKT',   # Damlakent GMS → BIST:DMLKT
        'ALTINS': 'ALTIN',   # Altın Sertifikası → BIST:ALTIN
        'SOHOEH': 'SOHOE',   # Rüçhan/"H" kodu → BIST:SOHOE
        'GOLDAH': 'GOLDA',   # Rüçhan/"H" kodu → BIST:GOLDA
    }

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        print('  yfinance not installed — stock prices skipped')
        return {}

    yf_tickers  = [t for t in tickers if t not in TV_FALLBACK]
    tv_tickers  = [t for t in tickers if t in TV_FALLBACK]
    is_tickers  = [f'{t}.IS' for t in yf_tickers]
    result: dict = {}

    # ── Yahoo Finance ──────────────────────────────────────────────────────
    if is_tickers:
        try:
            df = yf.download(
                is_tickers,
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                progress=False,
            )
            if not df.empty:
                close_df = df['Close'] if isinstance(df.columns, pd.MultiIndex) else \
                           df[['Close']].rename(columns={'Close': is_tickers[0]})
                for ticker in yf_tickers:
                    col = f'{ticker}.IS'
                    if col in close_df.columns:
                        series = close_df[col].dropna()
                        result[ticker] = {
                            str(idx.date()): round(float(v), 4)
                            for idx, v in series.items()
                        }
        except Exception as e:
            print(f'  yfinance error: {e}')

    # ── TradingView WebSocket fallback (no package needed, uses websocket-client) ──
    if tv_tickers:
        for ticker in tv_tickers:
            tv_sym = TV_FALLBACK[ticker]
            try:
                prices_tv = _fetch_tv_ws(tv_sym, 'BIST', n_bars=300)
                result[ticker] = {
                    d: p for d, p in prices_tv.items()
                    if d >= start_date.isoformat()
                }
            except Exception as e:
                print(f'  TV WebSocket {ticker} ({tv_sym}) error: {e}')

    return result


def fetch_benchmark_prices(start_date: date, end_date: date) -> dict:
    """
    Fetch benchmark comparison series:
      - xu100 : BIST 100 index daily closes (yfinance XU100.IS)
      - altin  : Gram TRY gold daily closes (TradingView FX_IDC:XAUTRYG)
    Returns { 'xu100': {date_str: price}, 'altin': {date_str: price} }.
    """
    from datetime import timedelta
    result: dict = {'xu100': {}, 'altin': {}}

    # ── XU100 (BIST 100) via yfinance ─────────────────────────────────────
    try:
        import yfinance as yf
        df = yf.download(
            'XU100.IS',
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            auto_adjust=True, progress=False,
        )
        if not df.empty:
            close = df['Close']
            # With MultiIndex columns (yfinance >= 0.2.x), df['Close'] is a DataFrame
            if hasattr(close, 'columns'):
                close = close.iloc[:, 0]
            result['xu100'] = {
                str(idx)[:10]: round(float(v), 2)
                for idx, v in close.dropna().items()
            }
            print(f'  XU100: {len(result["xu100"])} data points')
    except Exception as e:
        print(f'  XU100 fetch error: {e}')

    # ── Gram TRY Altın via TradingView (FX_IDC:XAUTRYG) ──────────────────
    try:
        prices_tv = _fetch_tv_ws('XAUTRYG', 'FX_IDC', n_bars=300)
        result['altin'] = {
            d: p for d, p in prices_tv.items()
            if d >= start_date.isoformat()
        }
        print(f'  Gram Altın (XAUTRYG): {len(result["altin"])} data points')
    except Exception as e:
        print(f'  XAUTRYG TradingView error: {e}')

    return result


def build_portfolio_daily_value(trades: list, nsp_trades: list, nsp_daily_value: list,
                                 stock_prices: dict) -> list:
    """
    Compute daily total portfolio value = Σ(qty_held × price) + NSP value + theoretical cash.

    "Theoretical cash" tracks in-transit money between portfolio rotations to eliminate
    artificial dips when e.g. an NSP sell settles before the replacement stocks are bought,
    or when stocks are sold and the proceeds sit idle before an NSP buy.
    """
    if not nsp_daily_value and not stock_prices:
        return []

    nsp_by_date = {p['date']: p['value'] for p in nsp_daily_value}

    # Build step-function NSP fill: for a date with no NSP price,
    # carry forward the last known NSP value.
    nsp_steps = sorted(nsp_by_date.items())

    def nsp_value_at(d_str: str) -> float:
        val = 0.0
        for step_d, step_v in nsp_steps:
            if step_d <= d_str:
                val = step_v
            else:
                break
        return val

    # Running stock inventory (sorted trades, applied in order)
    sorted_trades = sorted(trades, key=lambda x: x['date'])
    inventory: dict = defaultdict(int)
    trade_idx = 0

    # ── Theoretical cash ────────────────────────────────────────────────────
    # Tracks all cash inflows (sells) and outflows (buys) by trade date.
    # This compensates for the gap when one leg of a rotation settles before the other:
    #   NSP sell (T+0) → stocks appear at trade date → theoretical_cash bridges the gap
    #   Stock sell (T) → NSP buy later → theoretical_cash bridges the reverse gap
    cash_events: list = []
    for nt in nsp_trades:
        amount = nt['units'] * nt['price']
        cash_events.append((nt['date'], +amount if nt['type'] == 'satis' else -amount))
    for t in trades:
        cash_events.append((t['date'], +t['amount'] if t['type'] == 'satis' else -t['amount']))
    cash_events.sort()

    # Normalise: theoretical_cash = 0 on the first trade date so that the initial
    # deposit is already "absorbed" by stock_val + nsp_val on that day.
    first_trade_date_str = sorted_trades[0]['date'] if sorted_trades else ''
    raw_at_start = sum(delta for d, delta in cash_events
                       if first_trade_date_str and d <= first_trade_date_str)
    initial_cash_offset = -raw_at_start

    def theoretical_cash_at(d_str: str) -> float:
        running = sum(delta for d, delta in cash_events if d <= d_str)
        return initial_cash_offset + running

    # Union of all dates from stock prices + NSP
    all_dates: set = set(nsp_by_date.keys())
    for tp in stock_prices.values():
        all_dates.update(tp.keys())
    all_dates_sorted = sorted(all_dates)

    if not all_dates_sorted:
        return []

    # First trade date — don't show before portfolio started
    first_trade_date = first_trade_date_str or all_dates_sorted[0]

    result = []
    for d_str in all_dates_sorted:
        if d_str < first_trade_date:
            continue

        # Advance inventory to include all trades up to this date
        while trade_idx < len(sorted_trades) and sorted_trades[trade_idx]['date'] <= d_str:
            t = sorted_trades[trade_idx]
            if t['type'] == 'alis':
                inventory[t['ticker']] += t['qty']
            else:
                inventory[t['ticker']] -= t['qty']
            trade_idx += 1

        # Stock value on this date
        stock_val = 0.0
        for ticker, qty in inventory.items():
            if qty > 0 and ticker in stock_prices:
                price = stock_prices[ticker].get(d_str)
                if price:
                    stock_val += qty * price

        nsp_val  = nsp_value_at(d_str)
        th_cash  = theoretical_cash_at(d_str)
        total    = round(stock_val + nsp_val + th_cash, 2)

        if total > 0:
            result.append({
                'date':        d_str,
                'stock_value': round(stock_val, 2),
                'nsp_value':   round(nsp_val, 2),
                'cash_value':  round(th_cash, 2),
                'total_value': total,
            })

    return result


def main():
    pdf_path = os.path.abspath(PDF_PATH)
    if not os.path.exists(pdf_path):
        # Try the Downloads folder directly
        pdf_path = os.path.join(os.path.expanduser('~'), 'Downloads', 'Ekstre3 (1).pdf')

    print(f'Parsing {pdf_path}')
    rows = extract_lines(pdf_path)
    print(f'Found {len(rows)} transaction rows')

    trades, nsp_trades, nsp_position_history, dividends, balance_history, total_komis = process(rows)
    pnl_summary, open_positions, pnl_timeline = compute_pnl(trades)

    total_realized = sum(v['realized_pnl'] for v in pnl_summary.values())
    total_divs     = sum(d['net'] for d in dividends)

    # Fetch NSP daily prices from TEFAS for the portfolio period
    if nsp_position_history:
        first_nsp_date = date.fromisoformat(nsp_position_history[0]['date'])
        nsp_end_date   = date.today()
        print(f'Fetching NSP prices from TEFAS ({first_nsp_date} to {nsp_end_date})...')
        nsp_prices = fetch_nsp_prices(first_nsp_date, nsp_end_date)
        print(f'  Got {len(nsp_prices)} NSP price points')
    else:
        nsp_prices = {}

    nsp_daily_value = build_nsp_daily_value(nsp_position_history, nsp_prices)

    # For NSP transactions that fall inside a TEFAS data gap (API returned no prices
    # for that chunk), add synthetic nsp_daily_value entries using the transaction
    # price so that the portfolio chart doesn't show false dips or spikes when the
    # theoretical-cash change has no matching NSP value update.
    if nsp_position_history:
        nsp_daily_dict = {e['date']: e for e in nsp_daily_value}
        synthetic_added = 0
        for pos in nsp_position_history:
            if pos['date'] not in nsp_daily_dict:
                nsp_daily_value.append({
                    'date':  pos['date'],
                    'units': round(pos['units'], 3),
                    'price': round(pos['price'], 6),
                    'value': round(pos['units'] * pos['price'], 2),
                })
                synthetic_added += 1
        if synthetic_added:
            nsp_daily_value.sort(key=lambda x: x['date'])
            print(f'  Added {synthetic_added} synthetic NSP entries for TEFAS data gaps')

    nsp_current_value = nsp_daily_value[-1]['value'] if nsp_daily_value else (
        nsp_position_history[-1]['value'] if nsp_position_history else 0
    )

    # Fetch historical BIST closing prices for all traded tickers
    all_tickers = sorted({t['ticker'] for t in trades})
    if trades and all_tickers:
        first_trade_date = date.fromisoformat(sorted(trades, key=lambda x: x['date'])[0]['date'])
        print(f'Fetching BIST prices for {all_tickers} ...')
        stock_prices = fetch_stock_prices(all_tickers, first_trade_date, date.today())
        fetched = {t: len(v) for t, v in stock_prices.items()}
        print(f'  Price points: {fetched}')
    else:
        stock_prices = {}

    portfolio_daily_value = build_portfolio_daily_value(trades, nsp_trades, nsp_daily_value, stock_prices)
    portfolio_current_value = portfolio_daily_value[-1]['total_value'] if portfolio_daily_value else 0

    # Last known price for each ticker (for unrealized P&L of open positions)
    last_prices = {
        ticker: sorted(prices.items())[-1][1]
        for ticker, prices in stock_prices.items()
        if prices
    }

    # Fetch benchmark comparison series (XU100, Gram Altın)
    if trades:
        first_trade_date_d = date.fromisoformat(sorted(trades, key=lambda x: x['date'])[0]['date'])
        print('Fetching benchmark prices (XU100, Gram Altin)...')
        benchmark_prices = fetch_benchmark_prices(first_trade_date_d, date.today())
    else:
        benchmark_prices = {}

    output = {
        'period':       '2025-11-13 / 2026-05-23',
        'account_name': 'AHMET EMİN TAHTACI',
        'account_no':   '73285',
        'broker':       'PUSULA Yatırım',
        'summary': {
            'total_realized_pnl': round(total_realized, 2),
            'total_dividends':    round(total_divs, 2),
            'total_commission':   round(total_komis, 2),
        },
        'trades':                trades,
        'nsp_trades':            nsp_trades,
        'nsp_position_history':  nsp_position_history,
        'nsp_daily_value':          nsp_daily_value,
        'nsp_current_units':        nsp_position_history[-1]['units'] if nsp_position_history else 0,
        'nsp_current_value':        round(nsp_current_value, 2),
        'portfolio_daily_value':    portfolio_daily_value,
        'portfolio_current_value':  round(portfolio_current_value, 2),
        'pnl_timeline':             pnl_timeline,
        'balance_history':       balance_history,
        'dividends':             dividends,
        'pnl_by_ticker':   {
            k: {
                **v,
                'realized_pnl': round(v['realized_pnl'], 2),
                'buy_amount':   round(v['buy_amount'], 2),
                'sell_amount':  round(v['sell_amount'], 2),
                'avg_buy':      round(v['buy_amount'] / v['buy_qty'], 4) if v['buy_qty'] else 0,
                'avg_sell':     round(v['sell_amount'] / v['sell_qty'], 4) if v['sell_qty'] else 0,
                'pnl_pct':      round(v['realized_pnl'] / v['buy_amount'] * 100, 2) if v['buy_amount'] else 0,
            }
            for k, v in sorted(pnl_summary.items())
        },
        'open_positions':    open_positions,
        'last_prices':      last_prices,
        'benchmark_prices': benchmark_prices,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f'Trades: {len(trades)}  Dividends: {len(dividends)}')
    print(f'Open positions: {list(open_positions.keys())}')
    print(f'Total realized P&L: {total_realized:,.2f} TL')
    print(f'Total dividends:    {total_divs:,.2f} TL')
    print(f'Total commission:   {total_komis:,.2f} TL')
    print(f'NSP daily points:       {len(nsp_daily_value)} | Current: {nsp_current_value:,.2f} TL')
    print(f'Portfolio daily points: {len(portfolio_daily_value)} | Current: {portfolio_current_value:,.2f} TL')
    print(f'Written: {OUT_PATH}')


if __name__ == '__main__':
    main()
