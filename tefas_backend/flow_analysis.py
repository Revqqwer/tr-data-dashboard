"""
Varlık sınıfı bazlı akış hesaplama motoru.

Temel formül:
  asset_flow(fon, varlık) = net_flow(fon) × composition_weight(fon, varlık) / 100
"""

import datetime
from typing import Optional
from sqlmodel import Session, select

from tefas_backend.database import FundComposition, FundFlow, FundMeta as FundMetaDB, FundDaily

ASSET_CLASSES: dict[str, list[str]] = {
    "Hisse Senedi (TR)":        ["hs"],
    "Hisse Senedi (Yabancı)":   ["yhs"],
    "Devlet Tahvili / Bono":    ["dt", "dot", "hb", "tr"],
    "Özel Sektör Borçlanma":    ["fb", "ost", "osks", "eut", "osdb"],
    "Kira Sertifikası":         ["kks", "kksd", "kkstl", "kksyd", "oksyd"],
    "Altın / Kıy. Maden":       ["khau", "khd", "khtl", "kh"],
    "Mevduat (TL)":             ["vdm", "vmtl"],
    "Mevduat (Döviz)":          ["vmd", "vmau", "vm", "vint"],
    "BYF / Fon Sepeti":         ["byf", "kmbyf", "ybyf", "bb", "yyf", "ymk", "fkb"],
    "Repo / Para Piyasası":     ["r", "t", "tpp", "bpp"],
    "Gayrimenkul":              ["gsykb", "gsyy", "gykb", "gyy"],
    "Diğer":                    ["gas", "kba", "yba", "ybkb", "ybosb",
                                  "d", "db", "km", "btaa", "btas",
                                  "kmkba", "kmkks", "kibd"],
}

SMALL_THRESHOLD = 100_000_000


def _get_latest_date(session: Session) -> Optional[datetime.date]:
    return session.exec(
        select(FundFlow.trade_date)
        .order_by(FundFlow.trade_date.desc())  # type: ignore
        .limit(1)
    ).first()


def _comp_weight(comp: FundComposition, fields: list[str]) -> float:
    return sum((getattr(comp, f, None) or 0.0) for f in fields)


def _load_flows(
    session: Session,
    target_date: datetime.date,
    fund_type: Optional[str] = None,
    category: Optional[str] = None,
) -> list[FundFlow]:
    q = select(FundFlow).where(
        FundFlow.trade_date == target_date,
        FundFlow.net_flow.isnot(None),  # type: ignore
    )
    if fund_type:
        q = q.where(FundFlow.fund_type == fund_type.upper())
    flows = session.exec(q).all()

    if category:
        cat_codes = {
            r.code
            for r in session.exec(
                select(FundMetaDB).where(FundMetaDB.category == category)
            ).all()
        }
        flows = [f for f in flows if f.code in cat_codes]

    return flows


def _load_compositions(
    session: Session,
    target_date: datetime.date,
    codes: list[str],
    lookback_days: int = 45,
) -> dict[str, FundComposition]:
    """Her fon için target_date'e en yakın (<=) kompozisyonu döndürür.
    Tam gün eşleşmesi yerine 45-günlük geriye bakış kullanır — bu sayede
    collect_range'in her 7 günde 1 çektiği composition backfill'de bile
    kapsama oranı yüksek kalır.
    """
    if not codes:
        return {}
    from_date = target_date - datetime.timedelta(days=lookback_days)
    rows = session.exec(
        select(FundComposition).where(
            FundComposition.trade_date >= from_date,  # type: ignore
            FundComposition.trade_date <= target_date,  # type: ignore
            FundComposition.code.in_(codes),  # type: ignore
        )
    ).all()
    # Her fon için en güncel kaydı tut
    latest: dict[str, FundComposition] = {}
    for r in rows:
        if r.code not in latest or r.trade_date > latest[r.code].trade_date:
            latest[r.code] = r
    return latest


def _merge_small(result: dict[str, float]) -> dict[str, float]:
    merged: dict[str, float] = {}
    other = result.get("Diğer", 0.0)
    for k, v in result.items():
        if k == "Diğer":
            continue
        if abs(v) < SMALL_THRESHOLD:
            other += v
        else:
            merged[k] = v
    if other:
        merged["Diğer"] = other
    return dict(sorted(merged.items(), key=lambda x: -abs(x[1])))


def asset_class_flow(
    session: Session,
    target_date: Optional[datetime.date] = None,
    fund_type: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    if target_date is None:
        target_date = _get_latest_date(session)
    if target_date is None:
        return {}

    flows = _load_flows(session, target_date, fund_type, category)
    comps = _load_compositions(session, target_date, [f.code for f in flows])

    result = {ac: 0.0 for ac in ASSET_CLASSES}
    covered_flow = 0.0
    uncovered_flow = 0.0

    for flow in flows:
        comp = comps.get(flow.code)
        net = flow.net_flow or 0.0
        if not comp:
            uncovered_flow += net
            continue
        total_weight = sum(_comp_weight(comp, fields) for fields in ASSET_CLASSES.values())
        if total_weight < 1.0:
            uncovered_flow += net
            continue
        covered_flow += net
        for ac_name, fields in ASSET_CLASSES.items():
            w = _comp_weight(comp, fields)
            result[ac_name] += net * w / 100.0

    return {
        "date": str(target_date),
        "flows": _merge_small(result),
        "total_flow": sum(result.values()),
        "covered_flow": covered_flow,
        "uncovered_flow": uncovered_flow,
    }


def asset_class_by_fund_type(
    session: Session,
    target_date: Optional[datetime.date] = None,
    category: Optional[str] = None,
) -> dict:
    if target_date is None:
        target_date = _get_latest_date(session)
    if target_date is None:
        return {}

    FUND_TYPES = ["YAT", "EMK", "BYF"]
    breakdown: dict[str, dict[str, float]] = {ac: {ft: 0.0 for ft in FUND_TYPES} for ac in ASSET_CLASSES}

    for ft in FUND_TYPES:
        flows = _load_flows(session, target_date, fund_type=ft, category=category)
        comps = _load_compositions(session, target_date, [f.code for f in flows])
        for flow in flows:
            comp = comps.get(flow.code)
            net = flow.net_flow or 0.0
            if not comp:
                continue
            total_weight = sum(_comp_weight(comp, fields) for fields in ASSET_CLASSES.values())
            if total_weight < 1.0:
                continue
            for ac_name, fields in ASSET_CLASSES.items():
                w = _comp_weight(comp, fields)
                breakdown[ac_name][ft] += net * w / 100.0

    result = []
    for ac_name, ft_dict in breakdown.items():
        total = sum(ft_dict.values())
        if abs(total) < SMALL_THRESHOLD:
            continue
        result.append({"asset_class": ac_name, "total": total, **ft_dict})
    result.sort(key=lambda x: -abs(x["total"]))

    return {"date": str(target_date), "breakdown": result}


def flow_by_category(
    session: Session,
    target_date: Optional[datetime.date] = None,
    fund_type: Optional[str] = None,
) -> dict:
    if target_date is None:
        target_date = _get_latest_date(session)
    if target_date is None:
        return {}

    flows = _load_flows(session, target_date, fund_type=fund_type)
    cat_map: dict[str, str] = {
        r.code: (r.category or "Diğer")
        for r in session.exec(select(FundMetaDB)).all()
    }

    agg: dict[str, dict] = {}
    for flow in flows:
        cat = cat_map.get(flow.code, "Diğer")
        ft = flow.fund_type or "?"
        key = f"{ft}||{cat}"
        if key not in agg:
            agg[key] = {"fund_type": ft, "category": cat, "net_flow": 0.0, "aum": 0.0, "count": 0}
        agg[key]["net_flow"] += flow.net_flow or 0.0
        agg[key]["aum"] += flow.aum or 0.0
        agg[key]["count"] += 1

    result = sorted(agg.values(), key=lambda x: -abs(x["net_flow"]))
    return {"date": str(target_date), "categories": result}


def category_asset_breakdown(
    session: Session,
    category: str,
    fund_type: Optional[str] = None,
    target_date: Optional[datetime.date] = None,
) -> dict:
    if target_date is None:
        target_date = _get_latest_date(session)
    if target_date is None:
        return {}

    data = asset_class_flow(session, target_date, fund_type=fund_type, category=category)
    total = data.get("total_flow", 0)
    flows_raw = data.get("flows", {})

    enriched = {
        k: {"flow": v, "pct": (v / total * 100) if total else 0}
        for k, v in flows_raw.items()
    }

    return {
        "date": str(target_date),
        "category": category,
        "fund_type": fund_type,
        "total_flow": total,
        "asset_breakdown": enriched,
    }


def asset_class_history(
    session: Session,
    days: int = 30,
    fund_type: Optional[str] = None,
    category: Optional[str] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> list[dict]:
    if start_date and end_date:
        dates = session.exec(
            select(FundFlow.trade_date)
            .distinct()
            .where(
                FundFlow.trade_date >= start_date,  # type: ignore
                FundFlow.trade_date <= end_date,    # type: ignore
            )
            .order_by(FundFlow.trade_date)  # type: ignore
        ).all()
    else:
        dates = session.exec(
            select(FundFlow.trade_date)
            .distinct()
            .order_by(FundFlow.trade_date.desc())  # type: ignore
            .limit(days)
        ).all()
        dates = sorted(dates)
    if not dates:
        return []
    min_date, max_date = dates[0], dates[-1]

    cat_codes: set[str] | None = None
    if category:
        cat_codes = {r.code for r in session.exec(
            select(FundMetaDB).where(FundMetaDB.category == category)
        ).all()}

    fq = select(FundFlow).where(
        FundFlow.trade_date >= min_date,
        FundFlow.trade_date <= max_date,
        FundFlow.net_flow.isnot(None),  # type: ignore
    )
    if fund_type:
        fq = fq.where(FundFlow.fund_type == fund_type.upper())
    all_flows = session.exec(fq).all()

    codes = list({f.code for f in all_flows})
    lookback = datetime.timedelta(days=45)
    all_comps = session.exec(
        select(FundComposition).where(
            FundComposition.trade_date >= min_date - lookback,
            FundComposition.trade_date <= max_date,
            FundComposition.code.in_(codes),  # type: ignore
        )
    ).all()
    # Her (code) için tarih sıralı liste: en güncel kompozisyonu bulmak için
    from collections import defaultdict
    comp_by_code: dict[str, list] = defaultdict(list)
    for c in all_comps:
        comp_by_code[c.code].append(c)
    for lst in comp_by_code.values():
        lst.sort(key=lambda x: x.trade_date)

    def _latest_comp_as_of(code: str, d) -> "FundComposition | None":
        lst = comp_by_code.get(code)
        if not lst:
            return None
        # En son <= d olan kompozisyon
        result_comp = None
        for c in lst:
            if c.trade_date <= d:
                result_comp = c
            else:
                break
        return result_comp

    result = []
    for d in dates:
        day_flows = [f for f in all_flows if f.trade_date == d]
        if cat_codes is not None:
            day_flows = [f for f in day_flows if f.code in cat_codes]

        ac_totals = {ac: 0.0 for ac in ASSET_CLASSES}
        total = 0.0
        for flow in day_flows:
            comp = _latest_comp_as_of(flow.code, d)
            net = flow.net_flow or 0.0
            if not comp:
                total += net
                continue
            total_weight = sum(_comp_weight(comp, fields) for fields in ASSET_CLASSES.values())
            if total_weight < 1.0:
                total += net
                continue
            total += net
            for ac_name, fields in ASSET_CLASSES.items():
                w = _comp_weight(comp, fields)
                ac_totals[ac_name] += net * w / 100.0

        row: dict = {"date": str(d), "total": total}
        for ac, v in ac_totals.items():
            if abs(v) >= 1e7:
                row[ac] = round(v, 0)
        result.append(row)

    return result


def category_history(
    session: Session,
    days: int = 30,
    fund_type: Optional[str] = None,
    top_n: int = 8,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> list[dict]:
    if start_date and end_date:
        dates = session.exec(
            select(FundFlow.trade_date)
            .distinct()
            .where(
                FundFlow.trade_date >= start_date,  # type: ignore
                FundFlow.trade_date <= end_date,    # type: ignore
            )
            .order_by(FundFlow.trade_date)  # type: ignore
        ).all()
    else:
        dates = session.exec(
            select(FundFlow.trade_date)
            .distinct()
            .order_by(FundFlow.trade_date.desc())  # type: ignore
            .limit(days)
        ).all()
        dates = sorted(dates)
    if not dates:
        return []
    min_date, max_date = dates[0], dates[-1]

    cat_map: dict[str, str] = {
        r.code: (r.category or "Diğer")
        for r in session.exec(select(FundMetaDB)).all()
    }

    fq = select(FundFlow).where(
        FundFlow.trade_date >= min_date,
        FundFlow.trade_date <= max_date,
        FundFlow.net_flow.isnot(None),  # type: ignore
    )
    if fund_type:
        fq = fq.where(FundFlow.fund_type == fund_type.upper())
    all_flows = session.exec(fq).all()

    cat_totals: dict[str, float] = {}
    for f in all_flows:
        cat = cat_map.get(f.code, "Diğer")
        cat_totals[cat] = cat_totals.get(cat, 0.0) + abs(f.net_flow or 0.0)
    top_cats = [k for k, _ in sorted(cat_totals.items(), key=lambda x: -x[1])[:top_n]]

    result = []
    for d in dates:
        row: dict = {"date": str(d)}
        day_flows = [f for f in all_flows if f.trade_date == d]
        cat_day: dict[str, float] = {}
        for f in day_flows:
            cat = cat_map.get(f.code, "Diğer")
            if cat in top_cats:
                cat_day[cat] = cat_day.get(cat, 0.0) + (f.net_flow or 0.0)
        for cat in top_cats:
            row[cat] = round(cat_day.get(cat, 0.0), 0)
        result.append(row)

    return result


def asset_class_contributors(
    session: Session,
    asset_class: str,
    target_date: Optional[datetime.date] = None,
    fund_type: Optional[str] = None,
    limit: int = 25,
) -> list[dict]:
    if target_date is None:
        target_date = _get_latest_date(session)
    if target_date is None or asset_class not in ASSET_CLASSES:
        return []

    fields = ASSET_CLASSES[asset_class]
    flows = _load_flows(session, target_date, fund_type)
    comps = _load_compositions(session, target_date, [f.code for f in flows])
    cat_map: dict[str, str] = {
        r.code: (r.category or "Diğer")
        for r in session.exec(select(FundMetaDB)).all()
    }

    result = []
    for flow in flows:
        comp = comps.get(flow.code)
        net = flow.net_flow or 0.0
        if not comp:
            continue
        total_w = sum(_comp_weight(comp, f) for f in ASSET_CLASSES.values())
        if total_w < 1.0:
            continue
        w = _comp_weight(comp, fields)
        if w < 0.5:
            continue
        contribution = net * w / 100.0
        result.append({
            "code": flow.code,
            "name": flow.fname,
            "fund_type": flow.fund_type,
            "category": cat_map.get(flow.code, "—"),
            "net_flow": round(net, 0),
            "weight_pct": round(w, 1),
            "contribution": round(contribution, 0),
            "aum": flow.aum,
        })

    result.sort(key=lambda x: -abs(x["contribution"]))
    return result[:limit]


def category_top_funds(
    session: Session,
    category: str,
    target_date: Optional[datetime.date] = None,
    fund_type: Optional[str] = None,
    limit: int = 15,
) -> list[dict]:
    if target_date is None:
        target_date = _get_latest_date(session)
    if target_date is None:
        return []

    flows = _load_flows(session, target_date, fund_type, category)
    comps = _load_compositions(session, target_date, [f.code for f in flows])

    result = []
    for flow in flows:
        net = flow.net_flow or 0.0
        comp = comps.get(flow.code)

        top_assets: list[dict] = []
        if comp:
            ac_weights = []
            for ac_name, ac_fields in ASSET_CLASSES.items():
                w = _comp_weight(comp, ac_fields)
                if w >= 1.5:
                    ac_weights.append({"name": ac_name, "weight": round(w, 1)})
            ac_weights.sort(key=lambda x: -x["weight"])
            top_assets = ac_weights[:4]

        result.append({
            "code": flow.code,
            "name": flow.fname,
            "fund_type": flow.fund_type,
            "net_flow": round(net, 0),
            "flow_pct": flow.flow_pct,
            "aum": flow.aum,
            "top_assets": top_assets,
        })

    result.sort(key=lambda x: -(x["net_flow"] or 0))
    return result[:limit]


def asset_class_flow_range(
    session: Session,
    start_date: datetime.date,
    end_date: datetime.date,
    fund_type: Optional[str] = None,
    category: Optional[str] = None,
) -> dict:
    history = asset_class_history(
        session, days=9999, fund_type=fund_type, category=category,
        start_date=start_date, end_date=end_date,
    )
    totals: dict[str, float] = {}
    total_flow = 0.0
    for row in history:
        total_flow += row.get("total", 0.0)
        for k, v in row.items():
            if k in ("date", "total"):
                continue
            totals[k] = totals.get(k, 0.0) + v

    return {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "days": len(history),
        "flows": _merge_small(totals),
        "total_flow": total_flow,
        "covered_flow": sum(totals.values()),
    }


def flow_by_category_range(
    session: Session,
    start_date: datetime.date,
    end_date: datetime.date,
    fund_type: Optional[str] = None,
) -> dict:
    q = select(FundFlow).where(
        FundFlow.trade_date >= start_date,  # type: ignore
        FundFlow.trade_date <= end_date,    # type: ignore
        FundFlow.net_flow.isnot(None),      # type: ignore
    )
    if fund_type:
        q = q.where(FundFlow.fund_type == fund_type.upper())
    flows = session.exec(q).all()

    cat_map: dict[str, str] = {
        r.code: (r.category or "Diğer")
        for r in session.exec(select(FundMetaDB)).all()
    }

    agg: dict[str, dict] = {}
    for flow in flows:
        cat = cat_map.get(flow.code, "Diğer")
        ft = flow.fund_type or "?"
        key = f"{ft}||{cat}"
        if key not in agg:
            agg[key] = {"fund_type": ft, "category": cat, "net_flow": 0.0, "aum": 0.0, "codes": set()}
        agg[key]["net_flow"] += flow.net_flow or 0.0
        agg[key]["aum"] += flow.aum or 0.0
        agg[key]["codes"].add(flow.code)

    result = [
        {
            "fund_type": v["fund_type"],
            "category": v["category"],
            "net_flow": v["net_flow"],
            "aum": v["aum"],
            "count": len(v["codes"]),
        }
        for v in agg.values()
    ]
    result.sort(key=lambda x: -abs(x["net_flow"]))
    return {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "days": (end_date - start_date).days + 1,
        "categories": result,
    }


def asset_class_contributors_range(
    session: Session,
    asset_class: str,
    start_date: datetime.date,
    end_date: datetime.date,
    fund_type: Optional[str] = None,
    limit: int = 25,
) -> list[dict]:
    if asset_class not in ASSET_CLASSES:
        return []

    comp_date = session.exec(
        select(FundComposition.trade_date)
        .where(FundComposition.trade_date <= end_date)  # type: ignore
        .order_by(FundComposition.trade_date.desc())    # type: ignore
        .limit(1)
    ).first()
    if not comp_date:
        return []

    q = select(FundFlow).where(
        FundFlow.trade_date >= start_date,  # type: ignore
        FundFlow.trade_date <= end_date,    # type: ignore
        FundFlow.net_flow.isnot(None),      # type: ignore
    )
    if fund_type:
        q = q.where(FundFlow.fund_type == fund_type.upper())
    all_flows = session.exec(q).all()

    flow_sum: dict[str, float] = {}
    fund_meta: dict[str, FundFlow] = {}
    for f in all_flows:
        flow_sum[f.code] = flow_sum.get(f.code, 0.0) + (f.net_flow or 0.0)
        fund_meta[f.code] = f

    codes = list(flow_sum.keys())
    comps = _load_compositions(session, comp_date, codes)
    cat_map: dict[str, str] = {
        r.code: (r.category or "Diğer")
        for r in session.exec(select(FundMetaDB)).all()
    }
    fields = ASSET_CLASSES[asset_class]

    result = []
    for code, net in flow_sum.items():
        comp = comps.get(code)
        if not comp:
            continue
        total_w = sum(_comp_weight(comp, f) for f in ASSET_CLASSES.values())
        if total_w < 1.0:
            continue
        w = _comp_weight(comp, fields)
        if w < 0.5:
            continue
        fm = fund_meta[code]
        result.append({
            "code": code,
            "name": fm.fname,
            "fund_type": fm.fund_type,
            "category": cat_map.get(code, "—"),
            "net_flow": round(net, 0),
            "weight_pct": round(w, 1),
            "contribution": round(net * w / 100.0, 0),
            "aum": fm.aum,
        })

    result.sort(key=lambda x: -abs(x["contribution"]))
    return result[:limit]


def category_top_funds_range(
    session: Session,
    category: str,
    start_date: datetime.date,
    end_date: datetime.date,
    fund_type: Optional[str] = None,
    limit: int = 15,
) -> list[dict]:
    cat_codes = {
        r.code
        for r in session.exec(
            select(FundMetaDB).where(FundMetaDB.category == category)
        ).all()
    }
    if not cat_codes:
        return []

    q = select(FundFlow).where(
        FundFlow.trade_date >= start_date,  # type: ignore
        FundFlow.trade_date <= end_date,    # type: ignore
        FundFlow.net_flow.isnot(None),      # type: ignore
        FundFlow.code.in_(cat_codes),       # type: ignore
    )
    if fund_type:
        q = q.where(FundFlow.fund_type == fund_type.upper())
    all_flows = session.exec(q).all()

    flow_sum: dict[str, float] = {}
    fund_meta: dict[str, FundFlow] = {}
    for f in all_flows:
        flow_sum[f.code] = flow_sum.get(f.code, 0.0) + (f.net_flow or 0.0)
        fund_meta[f.code] = f

    comp_date = session.exec(
        select(FundComposition.trade_date)
        .where(FundComposition.trade_date <= end_date)  # type: ignore
        .order_by(FundComposition.trade_date.desc())    # type: ignore
        .limit(1)
    ).first() or end_date
    comps = _load_compositions(session, comp_date, list(flow_sum.keys()))

    result = []
    for code, net in flow_sum.items():
        comp = comps.get(code)
        top_assets: list[dict] = []
        if comp:
            ac_weights = [
                {"name": ac, "weight": round(_comp_weight(comp, f), 1)}
                for ac, f in ASSET_CLASSES.items()
                if _comp_weight(comp, f) >= 1.5
            ]
            ac_weights.sort(key=lambda x: -x["weight"])
            top_assets = ac_weights[:4]

        fm = fund_meta[code]
        result.append({
            "code": code,
            "name": fm.fname,
            "fund_type": fm.fund_type,
            "net_flow": round(net, 0),
            "flow_pct": None,
            "aum": fm.aum,
            "top_assets": top_assets,
        })

    result.sort(key=lambda x: -(x["net_flow"] or 0))
    return result[:limit]


def populate_categories_from_composition(session: Session) -> dict:
    """FundMeta.category = NULL olan fonlar icin baskin varlik sinifini turet.

    Her fonun en son FundComposition kaydina bakarak en buyuk agirlikli
    varlik sinifini (>= %20) kategori olarak atar.
    """
    metas = session.exec(
        select(FundMetaDB).where(FundMetaDB.category.is_(None))  # type: ignore
    ).all()
    updated = 0
    for meta in metas:
        comp = session.exec(
            select(FundComposition)
            .where(FundComposition.code == meta.code)
            .order_by(FundComposition.trade_date.desc())  # type: ignore
            .limit(1)
        ).first()
        if not comp:
            continue
        best_ac, best_w = None, 0.0
        for ac_name, fields in ASSET_CLASSES.items():
            w = _comp_weight(comp, fields)
            if w > best_w:
                best_w, best_ac = w, ac_name
        if best_ac and best_w >= 20.0:
            meta.category = best_ac
            session.add(meta)
            updated += 1
    session.commit()
    return {"updated": updated, "total": len(metas)}
