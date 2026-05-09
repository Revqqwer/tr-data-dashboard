import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlmodel import Session, select
from tefas_backend.database import engine, FundDaily, FundFlow
import datetime

code  = 'GAL'
start = datetime.date(2026, 3, 10)
end   = datetime.date(2026, 4, 20)

with Session(engine) as db:
    rows = db.exec(
        select(FundDaily.trade_date, FundDaily.shares, FundDaily.price, FundDaily.aum)
        .where(FundDaily.code == code)
        .where(FundDaily.trade_date >= start)
        .where(FundDaily.trade_date <= end)
        .order_by(FundDaily.trade_date)
    ).all()
    flows = db.exec(
        select(FundFlow.trade_date, FundFlow.net_flow)
        .where(FundFlow.code == code)
        .where(FundFlow.trade_date >= start)
        .where(FundFlow.trade_date <= end)
        .order_by(FundFlow.trade_date)
    ).all()

flow_map = {f.trade_date: f.net_flow for f in flows}

print("Tarih        Pay Sayisi              Fiyat      AUM(B)  Net Akis(B)")
print("-" * 72)
prev_shares = None
for r in rows:
    nf   = flow_map.get(r.trade_date)
    sh   = "{:>20,.0f}".format(r.shares) if r.shares else "                 BOŞ"
    nf_s = "{:>+10.1f}B".format(nf / 1e9) if nf else "         ---"
    flag = ""
    if r.shares is None:
        flag = " << PAY BOŞ"
    elif prev_shares and abs(r.shares - prev_shares) > 1e9:
        flag = " << BUYUK ATLAMA"
    print("{:<12} {} {:>10.2f} {:>8.1f}B {}{}".format(
        str(r.trade_date), sh, r.price or 0,
        (r.aum or 0) / 1e9, nf_s, flag))
    prev_shares = r.shares
