"""
TEFAS Flow — Flask Blueprint
Tüm /api/leaderboard, /api/flow/*, /api/funds/*, /api/categories/* endpoint'leri
"""

import datetime
from flask import Blueprint, jsonify, request, session as flask_session
from sqlmodel import Session, select

from tefas_backend.database import (
    FundFlow, FundDaily, FundMeta, FundComposition,
    engine, init_db,
)
from tefas_backend import flow_analysis as fa

tefas_bp = Blueprint("tefas", __name__)

# DB tablolarını oluştur (uygulama başlarken çağrılır)
try:
    init_db()
except Exception:
    pass  # Tablo zaten varsa sorun değil


def _auth():
    """Login kontrolü — oturum yoksa 401 döner."""
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


def _serialize(obj):
    """JSON serializasyonu için datetime.date → str dönüşümü."""
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/leaderboard")
def leaderboard():
    err = _auth()
    if err:
        return err

    date_str = request.args.get("date")
    limit = min(int(request.args.get("limit", 50)), 200)
    fund_type = request.args.get("fund_type")

    with Session(engine) as db:
        q = select(FundFlow).where(FundFlow.net_flow.isnot(None))  # type: ignore

        if date_str:
            target_date = _parse_date(date_str)
            if target_date:
                q = q.where(FundFlow.trade_date == target_date)
        else:
            # En son tarihi bul
            latest = db.exec(
                select(FundFlow.trade_date)
                .order_by(FundFlow.trade_date.desc())  # type: ignore
                .limit(1)
            ).first()
            if latest:
                q = q.where(FundFlow.trade_date == latest)

        if fund_type:
            q = q.where(FundFlow.fund_type == fund_type.upper())

        # Hem en büyük giriş hem çıkış için tüm listeyi çek, JS tarafı işler
        q = q.order_by(FundFlow.net_flow.desc())  # type: ignore
        rows = db.exec(q).all()

    result = [
        {
            "date":      r.trade_date.isoformat() if r.trade_date else None,
            "code":      r.code,
            "name":      r.fname,
            "fund_type": r.fund_type,
            "net_flow":  r.net_flow,
            "flow_pct":  r.flow_pct,
            "aum":       r.aum,
        }
        for r in rows
    ]
    return jsonify(result)


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
    limit = int(request.args.get("limit", 25))
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
# Manuel veri toplama tetikleyici (sadece admin için)
# ---------------------------------------------------------------------------
@tefas_bp.route("/api/collect", methods=["POST"])
def collect_trigger():
    if not flask_session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    date_str = request.json.get("date") if request.json else None
    target = _parse_date(date_str) or datetime.date.today()

    try:
        # Import burada yapılıyor çünkü collector başladığında DB path'i gerekiyor
        from tefas_backend.collector import collect_day
        collect_day(target)
        return jsonify({"status": "ok", "date": target.isoformat()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
