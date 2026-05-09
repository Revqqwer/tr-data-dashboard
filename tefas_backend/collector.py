"""
TEFAS fon verisi toplayıcı — tr-data-dashboard entegre sürümü.

Kullanım (PythonAnywhere bash konsolundan):
  cd /home/<kullanici>/tr-data-dashboard
  python tefas_backend/collector.py                    # bugünü çek
  python tefas_backend/collector.py --backfill 90      # son 90 gün
  python tefas_backend/collector.py --date 2025-04-01  # belirli bir tarih
"""

import os
import sys

# tr-data-dashboard root'unu Python path'e ekle
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import argparse
import datetime
import logging
import time
from typing import Optional

import requests
from sqlmodel import Session, select

from tefas_backend.database import (
    FundComposition, FundDaily, FundFlow, FundMeta,
    engine, init_db,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TEFAS_BASE = "https://www.tefas.gov.tr/api/funds"
BULK_ENDPOINT = "/fonGnlBlgSiraliGetirDosya"
COMPOSITION_ENDPOINT = "/dagilimSiraliGetirDosya"
FUND_TYPES = ["YAT", "EMK", "BYF"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tefas.gov.tr/",
}

_session: Optional[requests.Session] = None
_request_count = 0
SESSION_REFRESH_EVERY = 30


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.tefas.gov.tr/", timeout=15)
    time.sleep(1)
    return s


def _get_session() -> requests.Session:
    global _session, _request_count
    if _session is None or _request_count >= SESSION_REFRESH_EVERY:
        log.debug("Session yenileniyor...")
        _session = _new_session()
        _request_count = 0
    return _session


def _build_payload(fund_type: str, start_str: str, end_str: str | None = None) -> dict:
    return {
        "dil": "TR",
        "fonTipi": fund_type,
        "islem": 1,
        "basTarih": start_str,
        "bitTarih": end_str or start_str,
        "kurucuKodu": None,
        "sfonTurKod": None,
        "fonTurAciklama": None,
        "fonTurKod": None,
        "fonGrubu": None,
        "donemGetiri1a": "1",
        "donemGetiri3a": "1",
        "donemGetiri6a": "1",
        "donemGetiri1y": "1",
        "donemGetiriyb": "1",
        "donemGetiri3y": "1",
        "donemGetiri5y": "1",
    }


def fetch_bulk(fund_type: str, date: datetime.date,
               end_date: datetime.date | None = None) -> list[dict]:
    global _request_count
    start_str = date.strftime("%Y%m%d")
    end_str   = end_date.strftime("%Y%m%d") if end_date else None
    payload   = _build_payload(fund_type, start_str, end_str)

    for attempt in range(3):
        try:
            s = _get_session()
            resp = s.post(f"{TEFAS_BASE}{BULK_ENDPOINT}", json=payload, timeout=30)
            _request_count += 1

            if not resp.text.strip():
                log.debug("Boş yanıt (%s %s), session yenileniyor...", date, fund_type)
                _session = None
                _request_count = SESSION_REFRESH_EVERY
                time.sleep(3 + attempt * 2)
                continue

            resp.raise_for_status()
            data = resp.json()
            return data.get("resultList") or []

        except Exception as e:
            if attempt < 2:
                wait = 5 + attempt * 5
                log.warning("Hata (%s %s, deneme %d): %s — %ds beklenecek",
                            date, fund_type, attempt + 1, e, wait)
                _session = None
                _request_count = SESSION_REFRESH_EVERY
                time.sleep(wait)
            else:
                raise

    return []


def fetch_composition(date: datetime.date, fund_type: str = "YAT") -> list[dict]:
    global _request_count
    date_str = date.strftime("%Y%m%d")
    payload = _build_payload(fund_type, date_str)

    for attempt in range(3):
        try:
            s = _get_session()
            resp = s.post(f"{TEFAS_BASE}{COMPOSITION_ENDPOINT}", json=payload, timeout=30)
            _request_count += 1

            if not resp.text.strip():
                log.debug("Boş yanıt (composition %s), session yenileniyor...", date)
                _session = None
                _request_count = SESSION_REFRESH_EVERY
                time.sleep(3 + attempt * 2)
                continue

            resp.raise_for_status()
            data = resp.json()
            return data.get("resultList") or []

        except Exception as e:
            if attempt < 2:
                wait = 5 + attempt * 5
                log.warning("Hata (composition %s, deneme %d): %s — %ds beklenecek",
                            date, attempt + 1, e, wait)
                _session = None
                _request_count = SESSION_REFRESH_EVERY
                time.sleep(wait)
            else:
                raise

    return []


_COMP_FLOAT_FIELDS = [
    "bb", "byf", "d", "db", "bpp", "btaa", "btas", "dt", "dot", "eut",
    "fb", "fkb", "gas", "gsykb", "gsyy", "gykb", "gyy", "hb", "hs",
    "kba", "kh", "khau", "khd", "khtl", "kks", "kksd", "kkstl", "kksyd",
    "km", "kmbyf", "kmkba", "kmkks", "kibd", "osks", "ost", "r", "t",
    "tpp", "tr", "vdm", "vm", "vmau", "vmd", "vmtl", "vint",
    "yba", "ybkb", "ybosb", "ybyf", "yhs", "ymk", "yyf",
    "oksyd", "osdb", "bilFiyat",
]


def upsert_composition(session: Session, rows: list[dict]):
    for row in rows:
        tarih = row.get("tarih", "")
        if not tarih:
            continue
        try:
            d = datetime.date.fromisoformat(str(tarih)[:10])
        except (ValueError, TypeError):
            continue

        code = (row.get("fonKodu") or "").strip().upper()
        if not code:
            continue

        existing = session.exec(
            select(FundComposition).where(
                FundComposition.trade_date == d,
                FundComposition.code == code,
            )
        ).first()

        entry = existing or FundComposition(trade_date=d, code=code)
        entry.fname = row.get("fonUnvan") or entry.fname

        for field in _COMP_FLOAT_FIELDS:
            val = _parse_float(row.get(field))
            if val is not None or not existing:
                setattr(entry, field, val)

        session.add(entry)
    session.commit()


def _parse_float(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_int(val) -> Optional[int]:
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def upsert_daily(session: Session, rows: list[dict], fund_type: str):
    for row in rows:
        tarih = row.get("tarih", "")
        if not tarih:
            continue
        try:
            d = datetime.date.fromisoformat(str(tarih)[:10])
        except (ValueError, TypeError):
            continue

        code = (row.get("fonKodu") or "").strip().upper()
        if not code:
            continue

        existing = session.exec(
            select(FundDaily).where(
                FundDaily.trade_date == d,
                FundDaily.code == code,
            )
        ).first()

        entry = existing or FundDaily(trade_date=d, code=code)
        entry.fname = row.get("fonUnvan") or entry.fname
        entry.fund_type = fund_type
        entry.price = _parse_float(row.get("fiyat"))
        entry.aum = _parse_float(row.get("portfoyBuyukluk"))
        entry.shares = _parse_float(row.get("tedPaySayisi"))
        entry.investors = _parse_int(row.get("kisiSayisi"))
        session.add(entry)

        meta = session.get(FundMeta, code)
        if meta is None:
            meta = FundMeta(code=code)
        meta.fname = row.get("fonUnvan") or meta.fname
        meta.fund_type = fund_type
        # TEFAS API'nin döndürdüğü olası kategori alanları (biri varsa kullan)
        cat_val = (
            row.get("fonGrubu") or row.get("fonTurKod") or
            row.get("fonKategorisi") or row.get("altKategori") or
            row.get("fonTurAciklama")
        )
        if cat_val:
            meta.category = str(cat_val).strip()
        session.add(meta)

    session.commit()


def compute_flows(session: Session, target_date: datetime.date):
    prev_date = _prev_available_date(session, target_date)
    if prev_date is None:
        log.debug("Önceki tarih yok, flow atlanıyor: %s", target_date)
        return

    today_rows = session.exec(
        select(FundDaily).where(FundDaily.trade_date == target_date)
    ).all()

    prev_map: dict[str, FundDaily] = {
        r.code: r
        for r in session.exec(
            select(FundDaily).where(FundDaily.trade_date == prev_date)
        ).all()
    }

    for today in today_rows:
        prev = prev_map.get(today.code)

        net_flow: Optional[float] = None
        flow_pct: Optional[float] = None
        aum_change: Optional[float] = None

        if today.shares is not None and today.price is not None:
            if prev and prev.shares is not None:
                net_flow = (today.shares - prev.shares) * today.price
                if prev.aum and prev.aum != 0:
                    flow_pct = (net_flow / prev.aum) * 100

        if today.aum is not None and prev and prev.aum is not None:
            aum_change = today.aum - prev.aum

        existing = session.exec(
            select(FundFlow).where(
                FundFlow.trade_date == target_date,
                FundFlow.code == today.code,
            )
        ).first()

        entry = existing or FundFlow(trade_date=target_date, code=today.code)
        entry.fname = today.fname
        entry.fund_type = today.fund_type
        entry.net_flow = net_flow
        entry.flow_pct = flow_pct
        entry.aum_change = aum_change
        entry.aum = today.aum
        session.add(entry)

    session.commit()
    log.info("Flow hesaplandı: %s — %d fon", target_date, len(today_rows))


def _prev_available_date(session: Session, d: datetime.date) -> Optional[datetime.date]:
    return session.exec(
        select(FundDaily.trade_date)
        .where(FundDaily.trade_date < d)
        .order_by(FundDaily.trade_date.desc())  # type: ignore[attr-defined]
        .limit(1)
    ).first()


def collect_day(target_date: datetime.date, skip_composition: bool = False):
    init_db()
    with Session(engine) as session:
        for ft in FUND_TYPES:
            try:
                rows = fetch_bulk(ft, target_date)
                log.info("%s  %s: %d kayıt", target_date, ft, len(rows))
                upsert_daily(session, rows, ft)
                time.sleep(1.5)
            except Exception as e:
                log.error("%s  %s hatası: %s", target_date, ft, e)
        compute_flows(session, target_date)

        if not skip_composition:
            for ft in FUND_TYPES:
                try:
                    comp_rows = fetch_composition(target_date, ft)
                    log.info("%s  composition %s: %d fon", target_date, ft, len(comp_rows))
                    upsert_composition(session, comp_rows)
                    time.sleep(1.5)
                except Exception as e:
                    log.error("%s  composition %s hatası: %s", target_date, ft, e)


def collect_range(start: datetime.date, end: datetime.date,
                  skip_composition: bool = False, batch_days: int = 7):
    """
    Tarih aralığını batch_days'lik dilimler halinde çeker.
    Her adım kendi bağımsız Session'ını açıp kapatır — lock çakışması olmaz.
    """
    init_db()
    total = (end - start).days + 1
    done  = 0

    batch_start = start
    while batch_start <= end:
        batch_end = min(batch_start + datetime.timedelta(days=batch_days - 1), end)

        # 1) Bulk veri çek — her fon tipi için ayrı session
        for ft in FUND_TYPES:
            try:
                rows = fetch_bulk(ft, batch_start, batch_end)
                log.info("%s->%s  %s: %d kayit", batch_start, batch_end, ft, len(rows))
                with Session(engine) as session:
                    upsert_daily(session, rows, ft)
                time.sleep(1)
            except Exception as e:
                log.error("%s->%s  %s hatasi: %s", batch_start, batch_end, ft, e)

        # 2) Flow hesapla — her gün için ayrı session
        day = batch_start
        while day <= batch_end:
            try:
                with Session(engine) as session:
                    compute_flows(session, day)
            except Exception as e:
                log.error("Flow hatasi %s: %s", day, e)
            day += datetime.timedelta(days=1)

        done += (batch_end - batch_start).days + 1
        log.info("Ilerleme: %d/%d gun (%.1f%%)", done, total, done / total * 100)

        # 3) Composition — yalnizca batch sonu gunu, ayri session
        if not skip_composition:
            for ft in FUND_TYPES:
                try:
                    comp_rows = fetch_composition(batch_end, ft)
                    log.info("%s  composition %s: %d fon", batch_end, ft, len(comp_rows))
                    with Session(engine) as session:
                        upsert_composition(session, comp_rows)
                    time.sleep(1)
                except Exception as e:
                    log.error("%s  composition %s hatasi: %s", batch_end, ft, e)

        batch_start = batch_end + datetime.timedelta(days=1)
        time.sleep(0.5)


def collect_today():
    collect_day(datetime.date.today())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TEFAS veri toplayici")
    parser.add_argument("--backfill", type=int,
                        help="Son N gunu cek (ornek: --backfill 90)")
    parser.add_argument("--start", type=str, metavar="YYYY-MM-DD",
                        help="Bu tarihten bugune kadar cek (ornek: --start 2020-01-01)")
    parser.add_argument("--date", type=str, metavar="YYYY-MM-DD",
                        help="Tek bir gunu cek (ornek: --date 2025-04-01)")
    args = parser.parse_args()

    if args.start:
        start = datetime.date.fromisoformat(args.start)
        end   = datetime.date.today()
        elapsed_days = (end - start).days
        # Batch modunda tahmini sure: 3 istek/batch × 1s uyku + ~2s istek = ~15s/batch
        # batch_days=7 => elapsed_days/7 batch => elapsed_days/7 * 15s
        est_sec = (elapsed_days // 7 + 1) * 15 * 3
        log.info("Backfill basliyor: %s -> %s (%d gun, batch=7, tahmini sure: ~%d dakika)",
                 start, end, elapsed_days, est_sec // 60)
        collect_range(start, end, skip_composition=False, batch_days=7)
    elif args.backfill:
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=args.backfill)
        collect_range(start, end, batch_days=7)
    elif args.date:
        collect_day(datetime.date.fromisoformat(args.date))
    else:
        collect_today()
