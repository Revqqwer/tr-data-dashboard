"""
TEFAS Flow — Flask Blueprint
Tüm /api/leaderboard, /api/flow/*, /api/funds/*, /api/categories/* endpoint'leri
"""

import datetime
import json as _json
from pathlib import Path as _Path
from flask import Blueprint, jsonify, request, session as flask_session
from sqlmodel import Session, select
from sqlalchemy import func

from tefas_backend.database import (
    FundFlow, FundDaily, FundMeta, FundComposition,
    CryptoEtfFlow, engine, init_db,
)
from tefas_backend import flow_analysis as fa

tefas_bp = Blueprint("tefas", __name__)

# DB tablolarını oluştur (uygulama başlarken çağrılır)
try:
    init_db()
except Exception:
    pass  # Tablo zaten varsa sorun değil

# ---------------------------------------------------------------------------
# Fon adı bazlı kategori filtreleme — TEFAS fon isimleri büyük harfle gelir
# ---------------------------------------------------------------------------
_CATEGORY_PRESETS: dict = {
    "yogun":     {"label": "Hisse Yoğun",    "category": "Hisse Senedi Şemsiye Fonu"},
    "degisken":  {"label": "Değişken",       "category": "Değişken Şemsiye Fonu"},
    "para_piy":  {"label": "Para Piyasası",  "category": "Para Piyasası Şemsiye Fonu"},
    "tahvil":    {"label": "Tahvil / Bono",  "category": "Borçlanma Araçları Şemsiye Fonu"},
    "altin":     {"label": "Altın",          "category": "Kıymetli Madenler Şemsiye Fonu"},
    "karma":     {"label": "Karma",          "category": "Karma Şemsiye Fonu"},
    "serbest":   {"label": "Serbest",        "category": "Serbest Şemsiye Fonu"},
    "katilim":   {"label": "Katılım",        "category": "Katılım Şemsiye Fonu"},
    "fon_sepeti":{"label": "Fon Sepeti",     "category": "Fon Sepeti Şemsiye Fonu"},
}


def _auth():
    """Artık tüm okuma endpoint'leri public — her zaman None döner."""
    return None


def _require_auth():
    """Yazma / admin işlemler için zorunlu auth."""
    if not flask_session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _parse_date(s: str | None) -> datetime.date | None:
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _nearest_trade_date(db, target: datetime.date, *, forward: bool) -> datetime.date | None:
    """Hedef tarihte işlem yoksa (hafta sonu/tatil) en yakın işlem gününü bul."""
    q = select(FundDaily.trade_date).distinct()
    if forward:
        q = q.where(FundDaily.trade_date >= target).order_by(FundDaily.trade_date.asc())  # type: ignore
    else:
        q = q.where(FundDaily.trade_date <= target).order_by(FundDaily.trade_date.desc())  # type: ignore
    return db.exec(q.limit(1)).first()


def _price_return_map(db, start: datetime.date, end: datetime.date, codes: set) -> dict:
    """Verilen kodlar için start→end arası fiyat getirisi (%)."""
    if not codes:
        return {}
    price_start = {m.code: m.price for m in db.exec(
        select(FundDaily).where(
            FundDaily.trade_date == start,
            FundDaily.code.in_(codes),  # type: ignore
        )
    ).all()}
    price_end = {m.code: m.price for m in db.exec(
        select(FundDaily).where(
            FundDaily.trade_date == end,
            FundDaily.code.in_(codes),  # type: ignore
        )
    ).all()}
    out = {}
    for code in codes:
        p0, p1 = price_start.get(code), price_end.get(code)
        if p0 and p1:
            out[code] = round((p1 / p0 - 1) * 100, 2)
    return out


def _serialize(obj):
    """JSON serializasyonu için datetime.date → str dönüşümü."""
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
# Mevcut tarihler listesi (frontend tarih seçici için)
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/available-dates")
def available_dates():
    err = _auth()
    if err:
        return err

    limit = min(int(request.args.get("limit", 365)), 1000)

    with Session(engine) as db:
        dates = db.exec(
            select(FundFlow.trade_date)
            .distinct()
            .order_by(FundFlow.trade_date.desc())  # type: ignore
            .limit(limit)
        ).all()

    return jsonify([d.isoformat() for d in dates if d])


# ---------------------------------------------------------------------------
@tefas_bp.route("/api/leaderboard/categories")
def leaderboard_categories():
    """Mevcut kategori filtreleri listesi."""
    return jsonify([{"key": k, "label": v["label"]} for k, v in _CATEGORY_PRESETS.items()])


@tefas_bp.route("/api/leaderboard")
def leaderboard():
    err = _auth()
    if err:
        return err

    date_str  = request.args.get("date")
    start_str = request.args.get("start")
    end_str   = request.args.get("end")
    limit     = min(int(request.args.get("limit", 50)), 200)
    fund_type = request.args.get("fund_type")
    cat_keys       = [k for k in request.args.get("cat_key", "").split(",") if k.strip()]
    cat_categories = {_CATEGORY_PRESETS[k]["category"] for k in cat_keys if k in _CATEGORY_PRESETS}

    with Session(engine) as db:
        q = select(FundFlow).where(FundFlow.net_flow.isnot(None))  # type: ignore

        if start_str and end_str:
            # Dönem modu: tarihleri topla
            s = _parse_date(start_str)
            e = _parse_date(end_str)

            # Net akışı SQL tarafında topla (bellek dostu — tüm satırları çekme!)
            agg_q = select(FundFlow.code, func.sum(FundFlow.net_flow)).where(
                FundFlow.net_flow.isnot(None)  # type: ignore
            )
            if s and e:
                agg_q = agg_q.where(FundFlow.trade_date >= s, FundFlow.trade_date <= e)  # type: ignore
            if fund_type:
                agg_q = agg_q.where(FundFlow.fund_type == fund_type.upper())
            agg_q = agg_q.group_by(FundFlow.code)  # type: ignore
            flow_sum: dict = {code: (total or 0.0) for code, total in db.exec(agg_q).all()}

            sorted_codes = sorted(flow_sum.keys(), key=lambda c: -flow_sum[c])
            # Kategori filtresi (tek kolon seçimi → skaler döner, tuple değil)
            if cat_categories:
                cat_codes = set(db.exec(
                    select(FundMeta.code).where(FundMeta.category.in_(cat_categories))  # type: ignore
                ).all())
                sorted_codes = [c for c in sorted_codes if c in cat_codes]

            inflow_codes  = [c for c in sorted_codes if flow_sum[c] > 0][:limit]
            outflow_codes = [c for c in reversed(sorted_codes) if flow_sum[c] < 0][:limit]
            result_codes  = set(inflow_codes) | set(outflow_codes)

            # Meta (isim/tür): fund_meta küçük tablo
            meta = {c: (fn, ft) for c, fn, ft in db.exec(
                select(FundMeta.code, FundMeta.fname, FundMeta.fund_type)
            ).all()}

            # En güncel gün: aum + (meta'da yoksa) isim/tür — sadece sonuç kodları
            start_td = _nearest_trade_date(db, s, forward=True)  if s else None
            end_td   = _nearest_trade_date(db, e, forward=False) if e else None
            latest: dict = {}
            if result_codes and end_td:
                for c, fn, ft, aum in db.exec(
                    select(FundFlow.code, FundFlow.fname, FundFlow.fund_type, FundFlow.aum)
                    .where(FundFlow.trade_date == end_td, FundFlow.code.in_(result_codes))  # type: ignore
                ).all():
                    latest[c] = (fn, ft, aum)

            # Fiyat getirisi: dönem başı/sonu fiyat oranı
            price_return_map: dict = {}
            if start_td and end_td and start_td <= end_td:
                price_return_map = _price_return_map(db, start_td, end_td, result_codes)

            def row_to_dict_range(code):
                fn, ft, aum = latest.get(code, (None, None, None))
                if fn is None and code in meta:
                    fn, ft = meta[code]
                return {
                    "date":      end_str,
                    "code":      code,
                    "name":      fn,
                    "fund_type": ft,
                    "net_flow":  round(flow_sum[code], 0),
                    "price_return_pct": price_return_map.get(code),
                    "aum":       aum,
                }

            inflows  = [row_to_dict_range(c) for c in inflow_codes]
            outflows = [row_to_dict_range(c) for c in outflow_codes]
            range_label = f"{start_str}/{end_str}"
            return jsonify({"date": range_label, "inflows": inflows, "outflows": outflows})

        else:
            # Tek gün modu
            if date_str:
                target_date = _parse_date(date_str)
                if target_date:
                    q = q.where(FundFlow.trade_date == target_date)
            else:
                latest = db.exec(
                    select(FundFlow.trade_date)
                    .order_by(FundFlow.trade_date.desc())  # type: ignore
                    .limit(1)
                ).first()
                if latest:
                    q = q.where(FundFlow.trade_date == latest)

            if fund_type:
                q = q.where(FundFlow.fund_type == fund_type.upper())

            q = q.order_by(FundFlow.net_flow.desc())  # type: ignore
            rows = db.exec(q).all()

            # Kategori filtresi
            if cat_categories:
                cat_codes = {m.code for m in db.exec(
                    select(FundMeta).where(FundMeta.category.in_(cat_categories))  # type: ignore
                ).all()}
                rows = [r for r in rows if r.code in cat_codes]

            inflow_rows  = [r for r in rows if (r.net_flow or 0) > 0][:limit]
            outflow_rows = [r for r in reversed(rows) if (r.net_flow or 0) < 0][:limit]
            actual_date  = rows[0].trade_date if rows else None
            result_codes = {r.code for r in inflow_rows} | {r.code for r in outflow_rows}

            # Fiyat getirisi: önceki işlem gününe göre günlük getiri
            price_return_map: dict = {}
            if actual_date:
                prev_date = _nearest_trade_date(
                    db, actual_date - datetime.timedelta(days=1), forward=False
                )
                if prev_date and prev_date < actual_date:
                    price_return_map = _price_return_map(db, prev_date, actual_date, result_codes)

            def row_to_dict(r):
                return {
                    "date":      r.trade_date.isoformat() if r.trade_date else None,
                    "code":      r.code,
                    "name":      r.fname,
                    "fund_type": r.fund_type,
                    "net_flow":  r.net_flow,
                    "price_return_pct": price_return_map.get(r.code),
                    "aum":       r.aum,
                }

            inflows  = [row_to_dict(r) for r in inflow_rows]
            outflows = [row_to_dict(r) for r in outflow_rows]
            latest_date = actual_date.isoformat() if actual_date else None
            return jsonify({"date": latest_date, "inflows": inflows, "outflows": outflows})


# ---------------------------------------------------------------------------
# Özel Fonlar — admin tarafından seçilen fonların akış + getiri takibi
# ---------------------------------------------------------------------------
_CUSTOM_FUNDS_FILE = _Path(__file__).parent / "data" / "custom_funds.json"


def load_custom_funds() -> list:
    """Seçili özel fon kodlarını oku."""
    try:
        d = _json.loads(_CUSTOM_FUNDS_FILE.read_text(encoding="utf-8"))
        seen, out = set(), []
        for c in d.get("funds", []):
            c = str(c).strip().upper()
            if c and c not in seen:
                seen.add(c); out.append(c)
        return out
    except Exception:
        return []


def save_custom_funds(codes: list):
    """Özel fon listesini yaz (benzersiz, büyük harf)."""
    _CUSTOM_FUNDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    seen, clean = set(), []
    for c in codes:
        c = str(c).strip().upper()
        if c and c not in seen:
            seen.add(c); clean.append(c)
    _CUSTOM_FUNDS_FILE.write_text(
        _json.dumps({"funds": clean}, ensure_ascii=False, indent=2), encoding="utf-8")


def custom_funds_detail() -> list:
    """Kod + isim + kayıt var mı bilgisiyle özel fon listesi (admin için)."""
    codes = load_custom_funds()
    if not codes:
        return []
    with Session(engine) as db:
        names = {c: fn for c, fn in db.exec(
            select(FundMeta.code, FundMeta.fname).where(FundMeta.code.in_(codes))  # type: ignore
        ).all()}
        # fund_meta boşsa fund_daily'den son ismi dene
        missing = [c for c in codes if c not in names]
        if missing:
            for c, fn in db.exec(
                select(FundDaily.code, FundDaily.fname).where(FundDaily.code.in_(missing))  # type: ignore
            ).all():
                names.setdefault(c, fn)
    return [{"code": c, "name": names.get(c) or c, "found": c in names} for c in codes]


@tefas_bp.route("/api/custom-funds")
def custom_funds_list():
    """Seçili özel fonların listesi (kod + isim)."""
    err = _auth()
    if err:
        return err
    return jsonify([{"code": d["code"], "name": d["name"]} for d in custom_funds_detail()])


@tefas_bp.route("/api/custom-funds/flow")
def custom_funds_flow():
    """Seçili fonların kümülatif net akışı (fon bazında + TOPLAM) + getiri serisi."""
    err = _auth()
    if err:
        return err
    codes = load_custom_funds()
    if not codes:
        return jsonify({"funds": [], "flow": [], "ret": []})

    days      = request.args.get("days")
    start_str = request.args.get("start")
    end_str   = request.args.get("end")

    with Session(engine) as db:
        # Tarih penceresi
        s = _parse_date(start_str) if start_str else None
        e = _parse_date(end_str)   if end_str   else None
        if not (s and e) and days and days != "all":
            latest = db.exec(
                select(FundFlow.trade_date).where(FundFlow.code.in_(codes))  # type: ignore
                .order_by(FundFlow.trade_date.desc()).limit(1)
            ).first()
            if latest:
                e = latest
                s = latest - datetime.timedelta(days=int(days))

        # Akış satırları
        fq = select(FundFlow.code, FundFlow.trade_date, FundFlow.net_flow).where(
            FundFlow.code.in_(codes), FundFlow.net_flow.isnot(None)  # type: ignore
        )
        if s and e:
            fq = fq.where(FundFlow.trade_date >= s, FundFlow.trade_date <= e)  # type: ignore
        flows = db.exec(fq.order_by(FundFlow.trade_date)).all()

        # Fiyat satırları (getiri için)
        pq = select(FundDaily.code, FundDaily.trade_date, FundDaily.price).where(
            FundDaily.code.in_(codes), FundDaily.price.isnot(None)  # type: ignore
        )
        if s and e:
            pq = pq.where(FundDaily.trade_date >= s, FundDaily.trade_date <= e)  # type: ignore
        prices = db.exec(pq.order_by(FundDaily.trade_date)).all()

        names = {c: fn for c, fn in db.exec(
            select(FundMeta.code, FundMeta.fname).where(FundMeta.code.in_(codes))  # type: ignore
        ).all()}

    # ── Kümülatif net akış serisi (fon bazında + TOPLAM) ──
    flow_map: dict = {}
    for code, td, nf in flows:
        flow_map.setdefault(td, {})[code] = flow_map.get(td, {}).get(code, 0.0) + (nf or 0.0)
    flow_dates = sorted(flow_map.keys())
    flow_series = []
    cum = {c: 0.0 for c in codes}
    for d in flow_dates:
        total = 0.0
        gunluk = 0.0
        row = {"date": d.isoformat()}
        for c in codes:
            dv = flow_map[d].get(c, 0.0)
            gunluk += dv
            cum[c] += dv
            row[c] = round(cum[c], 0)
            total += cum[c]
        row["TOPLAM"]  = round(total, 0)
        row["GUNLUK"]  = round(gunluk, 0)   # o günkü toplam net akış (bar için)
        flow_series.append(row)

    # ── Getiri serisi (başlangıç = 100) ──
    price_map: dict = {}
    for code, td, px in prices:
        if px:
            price_map.setdefault(td, {})[code] = px
    ret_dates = sorted(price_map.keys())
    ret_series = []
    base: dict = {}
    last: dict = {}
    for d in ret_dates:
        row = {"date": d.isoformat()}
        for c in codes:
            px = price_map[d].get(c)
            if px:
                last[c] = px
                base.setdefault(c, px)
            if c in base and c in last:
                row[c] = round(last[c] / base[c] * 100, 2)
        ret_series.append(row)

    funds = [{"code": c, "name": names.get(c) or c} for c in codes]
    return jsonify({"funds": funds, "flow": flow_series, "ret": ret_series})


# ---------------------------------------------------------------------------
# Tekil fon akış serisi
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/funds/<code>/flow")
def fund_flow(code: str):
    err = _auth()
    if err:
        return err

    days = int(request.args.get("days", 30))
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    code = code.upper()

    with Session(engine) as db:
        q = select(FundFlow).where(FundFlow.code == code)

        if start_str and end_str:
            s = _parse_date(start_str)
            e = _parse_date(end_str)
            if s and e:
                q = q.where(
                    FundFlow.trade_date >= s,  # type: ignore
                    FundFlow.trade_date <= e,  # type: ignore
                )
        else:
            # Son N iş günü
            latest_dates = db.exec(
                select(FundFlow.trade_date)
                .distinct()
                .order_by(FundFlow.trade_date.desc())  # type: ignore
                .limit(days)
            ).all()
            if latest_dates:
                min_d = min(latest_dates)
                q = q.where(FundFlow.trade_date >= min_d)  # type: ignore

        q = q.order_by(FundFlow.trade_date)  # type: ignore
        rows = db.exec(q).all()

        # Aynı tarih aralığı için fiyat verisi (portföy getirisi hesabı için)
        if rows:
            min_d2 = rows[0].trade_date
            max_d2 = rows[-1].trade_date
            price_rows = db.exec(
                select(FundDaily.trade_date, FundDaily.price)
                .where(FundDaily.code == code)
                .where(FundDaily.trade_date >= min_d2)  # type: ignore
                .where(FundDaily.trade_date <= max_d2)  # type: ignore
                .order_by(FundDaily.trade_date)         # type: ignore
            ).all()
            price_map = {pr.trade_date: pr.price for pr in price_rows}
        else:
            price_map = {}

    result = [
        {
            "date":     r.trade_date.isoformat() if r.trade_date else None,
            "net_flow": r.net_flow,
            "flow_pct": r.flow_pct,
            "aum":      r.aum,
            "price":    price_map.get(r.trade_date),
        }
        for r in rows
    ]
    return jsonify(result)


# ---------------------------------------------------------------------------
# Tekil fon metadata
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/funds/<code>")
def fund_meta(code: str):
    err = _auth()
    if err:
        return err

    code = code.upper()
    with Session(engine) as db:
        meta = db.get(FundMeta, code)
        if not meta:
            return jsonify({"error": "Not found"}), 404

        # En son günlük veri
        latest = db.exec(
            select(FundDaily)
            .where(FundDaily.code == code)
            .order_by(FundDaily.trade_date.desc())  # type: ignore
            .limit(1)
        ).first()

    return jsonify({
        "code":      meta.code,
        "name":      meta.fname,
        "fund_type": meta.fund_type,
        "category":  meta.category,
        "price":     latest.price if latest else None,
        "aum":       latest.aum if latest else None,
        "investors": latest.investors if latest else None,
        "date":      latest.trade_date.isoformat() if latest and latest.trade_date else None,
    })


# ---------------------------------------------------------------------------
# Portföy dağılımı (composition)
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/funds/<code>/composition")
def fund_composition_route(code: str):
    err = _auth()
    if err:
        return err

    code = code.upper()
    days = int(request.args.get("days", 1))

    _SKIP = {"id", "code", "trade_date", "fname", "bilFiyat"}

    with Session(engine) as db:
        date_rows = db.exec(
            select(FundComposition.trade_date)
            .where(FundComposition.code == code)  # type: ignore
            .distinct()
            .order_by(FundComposition.trade_date.desc())  # type: ignore
            .limit(days)
        ).all()
        if not date_rows:
            return jsonify([])
        min_d = min(date_rows)
        rows = db.exec(
            select(FundComposition)
            .where(FundComposition.code == code)  # type: ignore
            .where(FundComposition.trade_date >= min_d)  # type: ignore
            .order_by(FundComposition.trade_date)  # type: ignore
        ).all()

    result = []
    for r in rows:
        entry = {"date": r.trade_date.isoformat()}
        row_dict = r.model_dump() if hasattr(r, "model_dump") else r.dict()
        for f, val in row_dict.items():
            if f not in _SKIP and isinstance(val, (int, float)) and val is not None and val != 0.0:
                entry[f] = round(float(val), 4)
        result.append(entry)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Tüm fonlar listesi
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/funds")
def funds_list():
    err = _auth()
    if err:
        return err

    fund_type = request.args.get("fund_type")
    q_str = request.args.get("q", "").strip().upper()

    with Session(engine) as db:
        q = select(FundMeta)
        if fund_type:
            q = q.where(FundMeta.fund_type == fund_type.upper())
        rows = db.exec(q).all()

    result = [
        {
            "code":      r.code,
            "name":      r.fname,
            "fund_type": r.fund_type,
            "category":  r.category,
        }
        for r in rows
        if not q_str or q_str in (r.code or "") or q_str in (r.fname or "").upper()
    ]
    return jsonify(result)


# ---------------------------------------------------------------------------
# Kategori bazlı akış
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/categories")
def categories():
    err = _auth()
    if err:
        return err

    date_str = request.args.get("date")
    fund_type = request.args.get("fund_type")
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    with Session(engine) as db:
        if start_str and end_str:
            s = _parse_date(start_str)
            e = _parse_date(end_str)
            if s and e:
                data = fa.flow_by_category_range(db, s, e, fund_type=fund_type)
                return jsonify(data)

        target = _parse_date(date_str)
        data = fa.flow_by_category(db, target_date=target, fund_type=fund_type)
        return jsonify(data)


# ---------------------------------------------------------------------------
# Varlık sınıfı akışı
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/asset-class")
def asset_class_flow():
    err = _auth()
    if err:
        return err

    date_str = request.args.get("date")
    fund_type = request.args.get("fund_type")
    category = request.args.get("category")
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    with Session(engine) as db:
        if start_str and end_str:
            s = _parse_date(start_str)
            e = _parse_date(end_str)
            if s and e:
                data = fa.asset_class_flow_range(db, s, e, fund_type=fund_type, category=category)
                return jsonify(data)

        target = _parse_date(date_str)
        data = fa.asset_class_flow(db, target_date=target, fund_type=fund_type, category=category)
        return jsonify(data)


# ---------------------------------------------------------------------------
# Varlık sınıfı × fon tipi breakdown
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/asset-class/by-fund-type")
def asset_class_by_fund_type():
    err = _auth()
    if err:
        return err

    date_str = request.args.get("date")
    category = request.args.get("category")

    with Session(engine) as db:
        target = _parse_date(date_str)
        data = fa.asset_class_by_fund_type(db, target_date=target, category=category)
        return jsonify(data)


# ---------------------------------------------------------------------------
# Varlık sınıfı katkı fonları
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/asset-class/contributors")
def asset_class_contributors():
    err = _auth()
    if err:
        return err

    asset_class = request.args.get("asset_class", "")
    date_str = request.args.get("date")
    fund_type = request.args.get("fund_type")
    limit = min(int(request.args.get("limit", 2000)), 5000)
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    with Session(engine) as db:
        if start_str and end_str:
            s = _parse_date(start_str)
            e = _parse_date(end_str)
            if s and e:
                data = fa.asset_class_contributors_range(
                    db, asset_class, s, e, fund_type=fund_type, limit=limit
                )
                return jsonify(data)

        target = _parse_date(date_str)
        data = fa.asset_class_contributors(
            db, asset_class, target_date=target, fund_type=fund_type, limit=limit
        )
        return jsonify(data)


# ---------------------------------------------------------------------------
# Varlık sınıfı tarihsel seri
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/asset-class/history")
def asset_class_history():
    err = _auth()
    if err:
        return err

    days = int(request.args.get("days", 30))
    fund_type = request.args.get("fund_type")
    category = request.args.get("category")
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    with Session(engine) as db:
        s = _parse_date(start_str)
        e = _parse_date(end_str)
        data = fa.asset_class_history(
            db, days=days, fund_type=fund_type, category=category,
            start_date=s, end_date=e,
        )
        return jsonify(data)


# ---------------------------------------------------------------------------
# Kategori tarihsel seri
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/category/history")
def category_history():
    err = _auth()
    if err:
        return err

    days = int(request.args.get("days", 30))
    fund_type = request.args.get("fund_type")
    top_n = int(request.args.get("top_n", 8))
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    with Session(engine) as db:
        s = _parse_date(start_str)
        e = _parse_date(end_str)
        data = fa.category_history(
            db, days=days, fund_type=fund_type, top_n=top_n,
            start_date=s, end_date=e,
        )
        return jsonify(data)


# ---------------------------------------------------------------------------
# Kategori top fonları
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/flow/category/top-funds")
def category_top_funds():
    err = _auth()
    if err:
        return err

    category = request.args.get("category", "")
    date_str = request.args.get("date")
    fund_type = request.args.get("fund_type")
    limit = int(request.args.get("limit", 15))
    start_str = request.args.get("start")
    end_str = request.args.get("end")

    with Session(engine) as db:
        if start_str and end_str:
            s = _parse_date(start_str)
            e = _parse_date(end_str)
            if s and e:
                data = fa.category_top_funds_range(
                    db, category, s, e, fund_type=fund_type, limit=limit
                )
                return jsonify(data)

        target = _parse_date(date_str)
        data = fa.category_top_funds(
            db, category, target_date=target, fund_type=fund_type, limit=limit
        )
        return jsonify(data)


# ---------------------------------------------------------------------------
# Global Piyasa Takip
# ---------------------------------------------------------------------------
# ── Market Agent ─────────────────────────────────────────────────────────────
@tefas_bp.route("/api/market-briefs")
def market_briefs():
    err = _auth()
    if err: return err
    report_type = request.args.get("type") or None  # None = tüm tipler
    limit = int(request.args.get("limit", 10))
    try:
        from tefas_backend.market_agent.reports import get_reports
        return jsonify({"ok": True, "reports": get_reports(report_type, limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@tefas_bp.route("/api/market-briefs/latest")
def market_briefs_latest():
    err = _auth()
    if err: return err
    report_type = request.args.get("type", "daily")
    try:
        from tefas_backend.market_agent.reports import get_latest
        return jsonify({"ok": True, "report": get_latest(report_type)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
@tefas_bp.route("/api/global-market")
def global_market_data():
    err = _auth()
    if err:
        return err
    try:
        from tefas_backend.global_market import get_all_data, get_4h_data, get_last_updated
        data       = get_all_data()
        data_4h    = get_4h_data()
        last_upd   = get_last_updated()
        return jsonify({"data": data, "data_4h": data_4h, "last_updated": last_upd})
    except Exception as e:
        return jsonify({"error": str(e), "data": {}, "data_4h": {}, "last_updated": None}), 500


@tefas_bp.route("/api/global-market/collect", methods=["POST"])
def global_market_collect():
    err = _require_auth()
    if err:
        return err
    try:
        from tefas_backend.global_market import collect_all
        force   = (request.json or {}).get("force", False)
        results = collect_all(force=force)
        ok      = sum(1 for v in results.values() if isinstance(v, int) and v > 0)
        skip    = sum(1 for v in results.values() if v == "skip")
        errors  = {k: v for k, v in results.items() if isinstance(v, str) and v != "skip"}
        return jsonify({"status": "ok", "updated": ok, "skipped": skip, "errors": errors})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Manuel veri toplama tetikleyici (sadece admin için)
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/collect", methods=["POST"])
def collect_trigger():
    err = _require_auth()
    if err: return err

    date_str = request.json.get("date") if request.json else None
    target = _parse_date(date_str) or datetime.date.today()

    try:
        # Import burada yapılıyor çünkü collector başladığında DB path'i gerekiyor
        from tefas_backend.collector import collect_day
        collect_day(target)
        return jsonify({"status": "ok", "date": target.isoformat()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Kategori migrasyon tetikleyici
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/migrate/categories", methods=["POST"])
def migrate_categories():
    """FundMeta.category = NULL fonlar icin kompozisyon bazli kategori ata."""
    err = _require_auth()
    if err: return err
    try:
        with Session(engine) as db:
            result = fa.populate_categories_from_composition(db)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Fund tip duzeltme tetikleyici
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/migrate/fix-fund-types", methods=["POST"])
def fix_fund_types():
    """TEFAS API'den gunceli cekip yanlis fund_type kaydedilmis fonlari duzeltir."""
    err = _require_auth()
    if err: return err
    try:
        from tefas_backend.fix_fund_types import fetch_all_fund_types, find_mismatches, apply_fixes
        date_str = (request.json or {}).get("date", datetime.date.today().strftime("%Y%m%d"))
        tefas_map = fetch_all_fund_types(date_str)
        mismatches = find_mismatches(tefas_map)
        apply_fixes(mismatches)
        return jsonify({
            "status": "ok",
            "tefas_fund_count": len(tefas_map),
            "fixed": len(mismatches),
            "details": [{"code": c, "old": o, "new": n} for c, o, n in mismatches],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Kripto ETF para akışları
# ---------------------------------------------------------------------------

@tefas_bp.route("/api/crypto/flows")
def crypto_flows():
    err = _auth()
    if err:
        return err

    asset    = request.args.get("asset", "BTC").upper()
    days_str = request.args.get("days")
    start_str = request.args.get("start")
    end_str   = request.args.get("end")

    with Session(engine) as db:
        q = select(CryptoEtfFlow).where(CryptoEtfFlow.asset == asset)

        if start_str and end_str:
            s = _parse_date(start_str)
            e = _parse_date(end_str)
            if s and e:
                q = q.where(CryptoEtfFlow.trade_date >= s, CryptoEtfFlow.trade_date <= e)
        elif days_str:
            try:
                n = int(days_str)
                latest = db.exec(
                    select(CryptoEtfFlow.trade_date)
                    .where(CryptoEtfFlow.asset == asset)
                    .order_by(CryptoEtfFlow.trade_date.desc())
                    .limit(1)
                ).first()
                if latest:
                    cutoff = latest - datetime.timedelta(days=n)
                    q = q.where(CryptoEtfFlow.trade_date >= cutoff)
            except ValueError:
                pass

        rows = db.exec(q.order_by(CryptoEtfFlow.trade_date.asc())).all()

    # Tarihe göre grupla
    from collections import defaultdict
    by_date: dict = defaultdict(lambda: {"tickers": {}, "total": 0.0})
    tickers_seen: list = []

    for r in rows:
        d = r.trade_date.isoformat()
        flow = r.flow_usd_m or 0.0
        by_date[d]["tickers"][r.ticker] = flow
        if r.ticker not in tickers_seen:
            tickers_seen.append(r.ticker)

    # Her gün total hesapla
    data = []
    for date_str in sorted(by_date.keys()):
        entry = by_date[date_str]
        total = sum(entry["tickers"].values())
        data.append({
            "date":    date_str,
            "total":   round(total, 2),
            "tickers": entry["tickers"],
        })

    return jsonify({"asset": asset, "tickers": tickers_seen, "data": data})


@tefas_bp.route("/api/crypto/collect", methods=["POST"])
def crypto_collect():
    err = _require_auth()
    if err: return err
    try:
        from tefas_backend.crypto_collector import collect_all
        results = collect_all()
        return jsonify({"status": "ok", "counts": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@tefas_bp.route("/api/crypto/import-excel", methods=["POST"])
def crypto_import_excel():
    err = _require_auth()
    if err: return err
    try:
        from tefas_backend.crypto_collector import import_from_excel
        filepath = (request.json or {}).get("filepath", "")
        if not filepath:
            return jsonify({"error": "filepath gerekli"}), 400
        counts = import_from_excel(filepath)
        return jsonify({"status": "ok", "counts": counts})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
