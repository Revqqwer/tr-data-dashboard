# -*- coding: utf-8 -*-
"""
Istatistikler sayfasindaki "BIST100'u gecen fon sayisi" serisinde belirli bir
tarih araligindaki ani degisimi ACIKLAR: her gun icin BIST getirisini, veri
olan fon sayisini, gecen fon sayisini ve fon getirilerinin dagilimini basar.
Boylece 'gercek piyasa hareketi mi, veri boslugu mu' netlesir.

Kullanim (PythonAnywhere Bash console):
  cd ~/tr-data-dashboard
  python scripts/diag_beat_window.py <donem_baslangic> <odak_bas> <odak_bit>
  ornek:
  python scripts/diag_beat_window.py 2026-02-20 2026-05-18 2026-05-26
  # 1. arg = donemin baslangici (kiyas bazi). Sayfadaki secili donemin basi.
"""
import sys
import os
import datetime
import bisect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select
from tefas_backend.database import engine, FundDaily, FundMeta
import tefas_api as TA


def main():
    a = sys.argv
    base = datetime.date.fromisoformat(a[1]) if len(a) > 1 else datetime.date(2026, 2, 20)
    f1 = datetime.date.fromisoformat(a[2]) if len(a) > 2 else datetime.date(2026, 5, 18)
    f2 = datetime.date.fromisoformat(a[3]) if len(a) > 3 else datetime.date(2026, 5, 26)
    CAT = "Hisse Senedi Şemsiye Fonu"

    with Session(engine) as db:
        codes = set(db.exec(select(FundMeta.code).where(FundMeta.category == CAT)).all())
        codes |= set(TA.load_stats_include())
        meta = {c: (fn, (ft or "").upper()) for c, fn, ft in db.exec(
            select(FundMeta.code, FundMeta.fname, FundMeta.fund_type).where(FundMeta.code.in_(list(codes)))
        ).all()}
        # ayni haric kurallari
        excl_ph = [TA._norm_name(p) for p in TA.load_stats_exclude()]
        excl_ty = {t.upper() for t in TA._STATS_EXCLUDE_TYPES}
        excl_cd = set(TA.load_stats_exclude_codes())
        inc = set(TA.load_stats_include())
        keep = set()
        for c in codes:
            if c in inc:
                keep.add(c); continue
            nm = TA._norm_name(meta.get(c, ("", ""))[0])
            ft = meta.get(c, ("", ""))[1]
            if (c in excl_cd) or (excl_ph and any(p in nm for p in excl_ph)) or (ft in excl_ty):
                continue
            keep.add(c)
        codes = keep

        rows = db.exec(
            select(FundDaily.code, FundDaily.trade_date, FundDaily.price).where(
                FundDaily.code.in_(list(codes)),
                FundDaily.price.isnot(None),
                FundDaily.trade_date >= base, FundDaily.trade_date <= f2,
            ).order_by(FundDaily.trade_date)
        ).all()

    # fon fiyat serileri
    pf_d, pf_p = {}, {}
    tmp = {}
    for c, td, px in rows:
        tmp.setdefault(c, []).append((td, px))
    for c, lst in tmp.items():
        lst.sort()
        pf_d[c] = [x[0] for x in lst]
        pf_p[c] = [x[1] for x in lst]

    def asof(c, d):
        arr = pf_d.get(c)
        if not arr:
            return None
        i = bisect.bisect_right(arr, d) - 1
        return pf_p[c][i] if i >= 0 else None

    # BIST100
    bist = TA._get_bist100_daily()
    bist = {datetime.date.fromisoformat(k): v for k, v in bist.items() if v}
    bdates = sorted(d for d in bist if base <= d <= f2)
    if not bdates:
        print("BIST100 verisi yok (TradingView). Once tv_ws calisiyor mu bak.")
        return
    base_d = bdates[0]
    bist_base = bist[base_d]

    fund_base = {c: asof(c, base_d) for c in codes}
    fund_base = {c: v for c, v in fund_base.items() if v and v > 0}
    print(f"Kiyas bazi: {base_d} | BIST={bist_base} | kiyaslanabilir fon: {len(fund_base)}")
    print(f"{'tarih':<12}{'BIST_kum%':>10}{'BIST_gun%':>10}{'fiyatOlan':>10}{'GECEN':>7}"
          f"{'fon_ort%':>10}{'fon_med%':>10}{'±1%bant':>9}")

    # her odak gunu icin (BIST gunleri)
    prev_bist = None
    prev_medyan = None
    for d in bdates:
        if not (f1 <= d <= f2):
            prev_bist = bist[d]
            continue
        bist_cum = bist[d] / bist_base - 1.0
        bist_gun = (bist[d] / prev_bist - 1.0) if prev_bist else 0.0
        rets = []
        have = 0
        gecen = 0
        for c, b in fund_base.items():
            px = asof(c, d)
            if px is None:
                continue
            have += 1
            r = px / b - 1.0
            rets.append(r)
            if r > bist_cum:
                gecen += 1
        rets.sort()
        ort = sum(rets) / len(rets) if rets else 0.0
        med = rets[len(rets) // 2] if rets else 0.0
        band = sum(1 for r in rets if abs(r - bist_cum) <= 0.01)
        print(f"{str(d):<12}{bist_cum*100:>9.2f}%{bist_gun*100:>9.2f}%{have:>10}{gecen:>7}"
              f"{ort*100:>9.2f}%{med*100:>9.2f}%{band:>9}")
        prev_bist = bist[d]

    print("\nOkuma: 'fiyatOlan' aniden dususe -> veri boslugu (artefakt). Stabilse -> gercek.")
    print("'±1%bant' buyukse fonlar BIST'e cok yakin kumelenmis -> sayi sert sicrar (normal).")
    print("'fon_med%' < 'BIST_kum%' oldugunda fonlar geride -> gecen sayisi duser.")


if __name__ == "__main__":
    main()
