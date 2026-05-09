"""
Fund tip düzeltme scripti.

TEFAS API'den güncel YAT/EMK/BYF sınıflandırmasını çekip,
veritabanındaki yanlış fund_type değerlerini düzeltir.

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
from sqlmodel import Session, select, update
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


def _tefas_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.tefas.gov.tr/", timeout=15)
    time.sleep(1)
    return s


def fetch_all_fund_types(date_str: str = "20250507") -> dict[str, str]:
    """
    TEFAS'tan YAT / EMK / BYF sorgularını çekip
    {fon_kodu: doğru_tip} sözlüğü döner.
    Çakışma yoksa her kod yalnızca bir tipte bulunur.
    """
    sess = _tefas_session()
    code_to_type: dict[str, str] = {}
    conflicts: dict[str, list[str]] = {}

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
            if not code:
                continue
            if code in code_to_type:
                # Çakışma — her iki tipi kaydet
                conflicts.setdefault(code, [code_to_type[code]]).append(ft)
            else:
                code_to_type[code] = ft

        time.sleep(1.5)

    if conflicts:
        log.warning("TEFAS'ta birden fazla tipte görünen fonlar: %s", conflicts)

    return code_to_type


def find_mismatches(tefas_map: dict[str, str]) -> list[tuple[str, str, str]]:
    """
    DB'deki fund_type ile TEFAS'ın verdiği tipi karşılaştır.
    [(code, db_type, correct_type), ...] döner.
    """
    mismatches = []
    with Session(engine) as db:
        metas = db.exec(select(FundMeta)).all()
        for m in metas:
            correct = tefas_map.get(m.code)
            if correct and m.fund_type != correct:
                mismatches.append((m.code, m.fund_type or "NULL", correct))
    return mismatches


def apply_fixes(mismatches: list[tuple[str, str, str]]):
    """FundMeta, FundDaily, FundFlow tablolarında fund_type'ı düzelt."""
    if not mismatches:
        log.info("Düzeltilecek kayıt yok.")
        return

    with Session(engine) as db:
        for code, old_type, new_type in mismatches:
            # FundMeta
            meta = db.get(FundMeta, code)
            if meta:
                meta.fund_type = new_type
                db.add(meta)

            # FundDaily — tüm tarihler
            daily_rows = db.exec(
                select(FundDaily).where(FundDaily.code == code)
            ).all()
            for row in daily_rows:
                row.fund_type = new_type
                db.add(row)

            # FundFlow — tüm tarihler
            flow_rows = db.exec(
                select(FundFlow).where(FundFlow.code == code)
            ).all()
            for row in flow_rows:
                row.fund_type = new_type
                db.add(row)

            log.info("Düzeltildi: %s  %s -> %s  (%d daily, %d flow)",
                     code, old_type, new_type, len(daily_rows), len(flow_rows))

        db.commit()

    log.info("Toplam %d fon düzeltildi.", len(mismatches))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fund tip düzeltici")
    parser.add_argument("--apply", action="store_true",
                        help="Değişiklikleri gerçekten uygula (varsayılan: dry-run)")
    parser.add_argument("--date", default="20250507",
                        help="TEFAS'tan hangi tarihin verisini kullan (YYYYMMDD)")
    args = parser.parse_args()

    log.info("TEFAS'tan güncel fon tipleri alınıyor...")
    tefas_map = fetch_all_fund_types(args.date)
    log.info("Toplam %d fon kodu alındı.", len(tefas_map))

    mismatches = find_mismatches(tefas_map)

    if not mismatches:
        print("\nSonuc: Tum fonlar dogru tipte. Duzeltme gerekmiyor.")
    else:
        print(f"\nYanlis tipte {len(mismatches)} fon bulundu:")
        print(f"{'KOD':<8} {'DB TIP':<6} {'DOGRU TIP'}")
        print("-" * 30)
        for code, old_t, new_t in sorted(mismatches):
            print(f"{code:<8} {old_t:<6} -> {new_t}")

        if args.apply:
            print("\nDuzeltmeler uygulanıyor...")
            apply_fixes(mismatches)
            print("Tamamlandi!")
        else:
            print("\nDry-run modu — gercekten uygulamak icin --apply ekle:")
            print("  python tefas_backend/fix_fund_types.py --apply")
