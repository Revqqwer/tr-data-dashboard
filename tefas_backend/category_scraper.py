"""
TEFAS Şemsiye Fon Türü — Kategori Scraper
==========================================
Tüm YAT / EMK / BYF fonlarının şemsiye türünü belirleyip
fund_meta.category kolonuna yazar.

Strateji
--------
TEFAS mevzuatı gereği her fon adı kendi şemsiye türünü içermek
zorundadır (ör. "HİSSE SENEDİ YOĞUN", "PARA PİYASASI", "KATILIM"…).
Bu nedenle TEFAS bulk API'den tüm fon adları çekilip
fname → şemsiye türü eşleşmesi yapılır; tek tek fon sayfası
scrape'e gerek yoktur.

Kullanım (PythonAnywhere bash):
    cd ~/tr-data-dashboard
    python tefas_backend/category_scraper.py           # hepsini güncelle
    python tefas_backend/category_scraper.py --dry-run # sadece raporla
    python tefas_backend/category_scraper.py --force   # zaten kategori
                                                        # olsun bile üstüne yaz
"""

import argparse
import datetime
import logging
import os
import sys
import time
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import requests
from sqlmodel import Session, select

from tefas_backend.database import FundMeta, FundDaily, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Şemsiye fon türü eşleşme kuralları
# Sıra önemli: daha spesifik kurallar önce gelir.
# Her kural: (fname'de aranacak anahtar kelime, normalleştirilmiş kategori adı)
# ---------------------------------------------------------------------------
FNAME_RULES: list[tuple[str, str]] = [
    # ── Spesifik / çakışma riski olanlar önce ──────────────────────────────
    ("FON SEPETİ",          "Fon Sepeti Şemsiye Fonu"),
    ("FON OF FUND",         "Fon Sepeti Şemsiye Fonu"),
    ("KATILIM",             "Katılım Şemsiye Fonu"),
    ("KARMA",               "Karma Şemsiye Fonu"),
    ("KIYMETLI MADEN",      "Kıymetli Madenler Şemsiye Fonu"),
    ("KIYMETLİ MADEN",      "Kıymetli Madenler Şemsiye Fonu"),
    ("GÜMÜŞ",               "Kıymetli Madenler Şemsiye Fonu"),
    ("GUMUS",               "Kıymetli Madenler Şemsiye Fonu"),
    ("ALTIN",               "Kıymetli Madenler Şemsiye Fonu"),
    ("BORÇLANMA ARAÇLARI",  "Borçlanma Araçları Şemsiye Fonu"),
    ("BORCLANMA ARACLARI",  "Borçlanma Araçları Şemsiye Fonu"),
    ("TAHVİL VE BONO",      "Borçlanma Araçları Şemsiye Fonu"),
    ("TAHVIL VE BONO",      "Borçlanma Araçları Şemsiye Fonu"),
    ("PARA PİYASASI",       "Para Piyasası Şemsiye Fonu"),
    ("PARA PIYASASI",       "Para Piyasası Şemsiye Fonu"),
    ("HİSSE SENEDİ",        "Hisse Senedi Şemsiye Fonu"),
    ("HISSE SENEDI",        "Hisse Senedi Şemsiye Fonu"),
    ("DEĞİŞKEN",            "Değişken Şemsiye Fonu"),
    ("DEGISKEN",            "Değişken Şemsiye Fonu"),
    # ── Genel ──────────────────────────────────────────────────────────────
    ("SERBEST",             "Serbest Şemsiye Fonu"),
]

# BYF fonları için ek kurallar (ETF'ler)
BYF_EXTRA_RULES: list[tuple[str, str]] = [
    ("BORSA YATIRIM FONU",  "Borsa Yatırım Fonu (BYF/ETF)"),
]

FUND_TYPES = ["YAT", "EMK", "BYF"]
TEFAS_BULK = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetirDosya"


def classify_fname(fname: str, fund_type: str = "YAT") -> Optional[str]:
    """Fon adından şemsiye türünü çıkar. Eşleşme bulunamazsa None döner."""
    upper = (fname or "").upper()
    rules = FNAME_RULES[:]
    if fund_type == "BYF":
        rules = BYF_EXTRA_RULES + rules  # BYF'e özgü kurallar önce
    for keyword, category in rules:
        if keyword in upper:
            return category
    return None


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tefas.gov.tr/",
    })
    s.get("https://www.tefas.gov.tr/", timeout=15)
    time.sleep(1)
    return s


def fetch_all_funds(s: requests.Session, target_date: datetime.date) -> dict[str, dict]:
    """Tüm fon tiplerini çek; {code: {fname, fund_type}} döner."""
    funds: dict[str, dict] = {}
    date_str = target_date.strftime("%Y%m%d")
    for ft in FUND_TYPES:
        payload = {
            "dil": "TR", "fonTipi": ft, "islem": 1,
            "basTarih": date_str, "bitTarih": date_str,
            "kurucuKodu": None, "sfonTurKod": None,
            "fonTurAciklama": None, "fonTurKod": None, "fonGrubu": None,
            "donemGetiri1a": "1",
        }
        for attempt in range(3):
            try:
                resp = s.post(TEFAS_BULK, json=payload, timeout=30)
                rows = resp.json().get("resultList") or []
                for r in rows:
                    code = (r.get("fonKodu") or "").strip().upper()
                    fname = r.get("fonUnvan") or ""
                    if code:
                        funds[code] = {"fname": fname, "fund_type": ft}
                log.info("%s — %s: %d fon", target_date, ft, len(rows))
                time.sleep(1.5)
                break
            except Exception as e:
                log.warning("Hata %s %s deneme %d: %s", ft, target_date, attempt + 1, e)
                if attempt == 2:
                    log.error("%s %s atlandı", ft, target_date)
                time.sleep(5)
    return funds


def scrape_categories(
    dry_run: bool = False,
    force: bool = False,
    target_date: Optional[datetime.date] = None,
) -> dict:
    """
    Ana fonksiyon.
    Returns: {"updated": N, "skipped": N, "unknown": N, "total": N}
    """
    if target_date is None:
        # En son veri tarihini DB'den al; yoksa bugünü kullan
        target_date = datetime.date.today()
        try:
            with Session(engine) as db:
                latest = db.exec(
                    select(FundDaily.trade_date)
                    .order_by(FundDaily.trade_date.desc())  # type: ignore
                    .limit(1)
                ).first()
                if latest:
                    target_date = latest
        except Exception:
            pass

    log.info("Hedef tarih: %s | dry_run=%s | force=%s", target_date, dry_run, force)

    s = _get_session()
    funds = fetch_all_funds(s, target_date)

    if not funds:
        # Fallback: DB'deki mevcut fname'leri kullan
        log.warning("API'den veri gelmedi, DB'deki fname'ler kullanılacak")
        try:
            with Session(engine) as db:
                rows = db.exec(
                    select(FundDaily.code, FundDaily.fname, FundDaily.fund_type)
                    .distinct()  # type: ignore
                ).all()
                for row in rows:
                    if row.code:
                        funds[row.code] = {"fname": row.fname or "", "fund_type": row.fund_type or "YAT"}
        except Exception as e:
            log.error("DB fallback hata: %s", e)

    log.info("Toplam %d fon işlenecek", len(funds))

    updated = skipped = unknown = 0

    with Session(engine) as db:
        for code, info in funds.items():
            fname     = info["fname"]
            fund_type = info["fund_type"]
            category  = classify_fname(fname, fund_type)

            if category is None:
                log.debug("Kategori bulunamadı: %s — %s", code, fname)
                unknown += 1
                continue

            # Mevcut kaydı kontrol et
            meta = db.get(FundMeta, code)
            if meta is None:
                meta = FundMeta(code=code, fname=fname, fund_type=fund_type)

            if meta.category and not force:
                # Zaten var, atla
                skipped += 1
                continue

            if not dry_run:
                meta.category   = category
                meta.fname      = meta.fname or fname
                meta.fund_type  = meta.fund_type or fund_type
                meta.updated_at = datetime.date.today()
                db.add(meta)
            updated += 1
            log.debug("%-6s %-60s → %s", code, fname[:60], category)

        if not dry_run:
            db.commit()

    total = len(funds)
    log.info(
        "Tamamlandı: güncellendi=%d atlandı=%d kategori_yok=%d toplam=%d",
        updated, skipped, unknown, total,
    )
    return {"updated": updated, "skipped": skipped, "unknown": unknown, "total": total}


def category_stats() -> list[dict]:
    """DB'deki mevcut kategori dağılımını döner."""
    with Session(engine) as db:
        rows = db.exec(select(FundMeta)).all()
    from collections import Counter
    counts = Counter(r.category or "—" for r in rows)
    return [{"category": cat, "count": cnt} for cat, cnt in counts.most_common()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TEFAS şemsiye fon kategorisi scraper")
    parser.add_argument("--dry-run", action="store_true", help="Sadece raporla, DB'ye yazma")
    parser.add_argument("--force",   action="store_true", help="Mevcut kategorilerin üstüne yaz")
    parser.add_argument("--date",    type=str, metavar="YYYY-MM-DD", help="Hedef tarih")
    parser.add_argument("--stats",   action="store_true", help="Mevcut kategori istatistiklerini göster")
    args = parser.parse_args()

    if args.stats:
        stats = category_stats()
        print("\n── Mevcut kategori dağılımı ──")
        for s in stats:
            print("  {:<50} {:>5} fon".format(s["category"], s["count"]))
        sys.exit(0)

    target_date = None
    if args.date:
        target_date = datetime.date.fromisoformat(args.date)

    result = scrape_categories(
        dry_run=args.dry_run,
        force=args.force,
        target_date=target_date,
    )
    print("\n── Sonuç ──")
    print("Güncellendi : {:>5}".format(result["updated"]))
    print("Atlandı     : {:>5}  (zaten vardı)".format(result["skipped"]))
    print("Eşleşmedi   : {:>5}  (fname'den tür çıkarılamadı)".format(result["unknown"]))
    print("Toplam      : {:>5}".format(result["total"]))
