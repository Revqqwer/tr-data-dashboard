"""
Fund tip düzeltme scripti.

İki aşamalı kontrol:
  1. TEFAS API'den güncel tip haritasını çekip FundMeta'yı güncelle
  2. DB-internal tutarsızlık: FundFlow / FundDaily kayıtlarında
     aynı fon kodunun farklı fund_type yazmasını düzelt

Kullanım (PythonAnywhere bash):
  cd ~/tr-data-dashboard
  python tefas_backend/fix_fund_types.py           # dry-run (sadece rapor)
  python tefas_backend/fix_fund_types.py --apply   # gerçekten düzelt
"""

import os, sys, time, logging, argparse
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import requests
from sqlmodel import Session, select
from tefas_backend.database import engine, FundMeta, FundDaily, FundFlow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TEFAS_BASE = "https://www.tefas.gov.tr/api/funds"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tefas.gov.tr/",
}


# ---------------------------------------------------------------------------
# Aşama 1 — TEFAS → FundMeta
# ---------------------------------------------------------------------------

def _tefas_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.tefas.gov.tr/", timeout=15)
    time.sleep(1)
    return s


def fetch_all_fund_types(date_str: str = "20250507") -> dict[str, str]:
    """TEFAS API'den {fon_kodu: dogru_tip} haritasını döner."""
    sess = _tefas_session()
    code_to_type: dict[str, str] = {}

    for ft in ["YAT", "EMK", "BYF"]:
        payload = {
            "dil": "TR", "fonTipi": ft, "islem": 1,
            "basTarih": date_str, "bitTarih": date_str,
            "kurucuKodu": None, "sfonTurKod": None,
            "fonTurAciklama": None, "fonTurKod": None, "fonGrubu": None,
            "donemGetiri1a": "1", "donemGetiri3a": "1", "donemGetiri6a": "1",
            "donemGetiri1y": "1", "donemGetiriyb": "1", "donemGetiri3y": "1", "donemGetiri5y": "1",
        }
        resp = sess.post(f"{TEFAS_BASE}/fonGnlBlgSiraliGetirDosya", json=payload, timeout=30)
        rows = resp.json().get("resultList", [])
        log.info("%s: %d fon", ft, len(rows))
        for r in rows:
            code = (r.get("fonKodu") or "").strip().upper()
            if code and code not in code_to_type:
                code_to_type[code] = ft
        time.sleep(1.5)

    return code_to_type


def find_meta_mismatches(tefas_map: dict[str, str]) -> list[tuple[str, str, str]]:
    """FundMeta.fund_type ≠ TEFAS güncel tipi olan fonlar."""
    mismatches = []
    with Session(engine) as db:
        for m in db.exec(select(FundMeta)).all():
            correct = tefas_map.get(m.code)
            if correct and m.fund_type != correct:
                mismatches.append((m.code, m.fund_type or "NULL", correct))
    return mismatches


# ---------------------------------------------------------------------------
# Aşama 2 — DB-internal tutarsızlık: FundFlow/FundDaily ≠ FundMeta
# ---------------------------------------------------------------------------

def find_internal_mismatches() -> list[tuple[str, str, str]]:
    """
    FundMeta.fund_type değeri ile FundFlow veya FundDaily'deki
    herhangi bir kayıt arasında uyumsuzluk olan fonları bulur.
    [(code, yanlis_tip, dogru_tip), ...]
    """
    mismatches = []
    with Session(engine) as db:
        # FundMeta'dan doğru tip haritası
        meta_map: dict[str, str] = {
            m.code: m.fund_type
            for m in db.exec(select(FundMeta)).all()
            if m.fund_type
        }

        # FundFlow'da tutarsız tipte kayıt olan fonları bul
        flow_wrong: dict[str, set[str]] = {}
        for row in db.exec(select(FundFlow.code, FundFlow.fund_type).distinct()).all():
            code, ft = row
            correct = meta_map.get(code)
            if correct and ft and ft != correct:
                flow_wrong.setdefault(code, set()).add(ft)

        for code, wrong_types in flow_wrong.items():
            mismatches.append((code, ", ".join(sorted(wrong_types)), meta_map[code]))

    return mismatches


# ---------------------------------------------------------------------------
# Düzeltme uygula
# ---------------------------------------------------------------------------

def apply_all_fixes(
    meta_mismatches: list[tuple[str, str, str]],
    internal_mismatches: list[tuple[str, str, str]],
) -> None:
    """FundMeta, FundDaily ve FundFlow tablolarını doğru tipe çeker."""

    # Tüm düzeltmeleri tek bir sözlükte topla: {code: correct_type}
    fixes: dict[str, str] = {}
    for code, _, correct in meta_mismatches:
        fixes[code] = correct
    for code, _, correct in internal_mismatches:
        fixes[code] = correct

    if not fixes:
        log.info("Duzeltilecek kayit yok.")
        return

    with Session(engine) as db:
        for code, new_type in fixes.items():
            old_meta_type = "?"
            # FundMeta
            meta = db.get(FundMeta, code)
            if meta:
                old_meta_type = meta.fund_type or "NULL"
                meta.fund_type = new_type
                db.add(meta)

            # FundDaily — tüm tarihler
            daily_rows = db.exec(
                select(FundDaily).where(FundDaily.code == code)
            ).all()
            d_fixed = 0
            for row in daily_rows:
                if row.fund_type != new_type:
                    row.fund_type = new_type
                    db.add(row)
                    d_fixed += 1

            # FundFlow — tüm tarihler
            flow_rows = db.exec(
                select(FundFlow).where(FundFlow.code == code)
            ).all()
            f_fixed = 0
            for row in flow_rows:
                if row.fund_type != new_type:
                    row.fund_type = new_type
                    db.add(row)
                    f_fixed += 1

            log.info("Duzeltildi: %-8s  %s -> %s  (daily=%d, flow=%d)",
                     code, old_meta_type, new_type, d_fixed, f_fixed)

        db.commit()

    log.info("Toplam %d fon duzeltildi.", len(fixes))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fund tip duzeltici")
    parser.add_argument("--apply", action="store_true",
                        help="Degisiklikleri gercekten uygula (varsayilan: dry-run)")
    parser.add_argument("--date", default="20250507",
                        help="TEFAS'tan hangi tarihin verisini kullan (YYYYMMDD)")
    parser.add_argument("--skip-tefas", action="store_true",
                        help="TEFAS API'ye baglanma, sadece DB-internal tutarsizligi duzelt")
    args = parser.parse_args()

    meta_mismatches: list[tuple[str, str, str]] = []

    if not args.skip_tefas:
        log.info("TEFAS'tan guncel fon tipleri aliniyor...")
        tefas_map = fetch_all_fund_types(args.date)
        log.info("Toplam %d fon kodu alindi.", len(tefas_map))
        meta_mismatches = find_meta_mismatches(tefas_map)
    else:
        log.info("TEFAS sorgusu atlandi (--skip-tefas).")

    log.info("DB-internal tutarsizlik taraniyor...")
    internal_mismatches = find_internal_mismatches()

    total = len(meta_mismatches) + len(internal_mismatches)
    if total == 0:
        print("\nSonuc: Her sey tutarli. Duzeltme gerekmiyor.")
        sys.exit(0)

    if meta_mismatches:
        print(f"\n[1] FundMeta <> TEFAS uyumsuzlugu: {len(meta_mismatches)} fon")
        print(f"  {'KOD':<8} {'MEVCUT':<8} -> DOGRU")
        for code, old, new in sorted(meta_mismatches):
            print(f"  {code:<8} {old:<8} -> {new}")

    if internal_mismatches:
        print(f"\n[2] FundFlow/Daily <> FundMeta uyumsuzlugu: {len(internal_mismatches)} fon")
        print(f"  {'KOD':<8} {'FLOW/DAILY TIP':<15} -> DOGRU (FundMeta)")
        for code, wrong, correct in sorted(internal_mismatches):
            print(f"  {code:<8} {wrong:<15} -> {correct}")

    if args.apply:
        print("\nDuzeltmeler uygulanıyor...")
        apply_all_fixes(meta_mismatches, internal_mismatches)
        print("Tamamlandi!")
    else:
        print(f"\nToplam {total} fon duzeltilecek.")
        print("Gercekten uygulamak icin --apply ekle:")
        print("  python tefas_backend/fix_fund_types.py --apply")
        print()
        print("Ya da sadece DB-internal duzeltme icin (daha hizli):")
        print("  python tefas_backend/fix_fund_types.py --skip-tefas --apply")
