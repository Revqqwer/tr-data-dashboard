#!/usr/bin/env python3
"""Parse broker account statement (Ekstre3.pdf) → data/portfolio.json"""
import pdfplumber
import re
import json
import os
from datetime import datetime, date
from collections import defaultdict

PDF_PATH  = os.path.join(os.path.dirname(__file__), '..', '..', 'Downloads', 'Ekstre3.pdf')
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
NSP_RE      = re.compile(r'NSP B Tipi')


def process(rows):
    trades        = []
    dividends     = []
    balance_pts   = {}   # date -> last bakiye
    total_komis   = 0.0

    for r in rows:
        d    = r['row_date']
        desc = r['description']
        balance_pts[d.isoformat()] = r['bakiye']

        # Skip NSP money-market fund (not BIST equities)
        if NSP_RE.search(desc):
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

    # Daily balance (keep last value per day, sorted)
    balance_history = [
        {'date': k, 'balance': v}
        for k, v in sorted(balance_pts.items())
    ]

    return trades, dividends, balance_history, total_komis


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


def main():
    pdf_path = os.path.abspath(PDF_PATH)
    if not os.path.exists(pdf_path):
        # Try the Downloads folder directly
        pdf_path = os.path.join(os.path.expanduser('~'), 'Downloads', 'Ekstre3.pdf')

    print(f'Parsing {pdf_path}')
    rows = extract_lines(pdf_path)
    print(f'Found {len(rows)} transaction rows')

    trades, dividends, balance_history, total_komis = process(rows)
    pnl_summary, open_positions, pnl_timeline = compute_pnl(trades)

    total_realized = sum(v['realized_pnl'] for v in pnl_summary.values())
    total_divs     = sum(d['net'] for d in dividends)

    output = {
        'period':       '2025-11-12 / 2026-05-07',
        'account_name': 'AHMET EMİN TAHTACI',
        'account_no':   '73285',
        'broker':       'PUSULA Yatırım',
        'summary': {
            'total_realized_pnl': round(total_realized, 2),
            'total_dividends':    round(total_divs, 2),
            'total_commission':   round(total_komis, 2),
        },
        'trades':          trades,
        'pnl_timeline':    pnl_timeline,
        'balance_history': balance_history,
        'dividends':       dividends,
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
        'open_positions':  open_positions,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f'Trades: {len(trades)}  Dividends: {len(dividends)}')
    print(f'Open positions: {list(open_positions.keys())}')
    print(f'Total realized P&L: {total_realized:,.2f} TL')
    print(f'Total dividends:    {total_divs:,.2f} TL')
    print(f'Total commission:   {total_komis:,.2f} TL')
    print(f'Written → {OUT_PATH}')


if __name__ == '__main__':
    main()
