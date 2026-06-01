"""
Market Agent — Rapor Depolama
JSON dosyalarına kaydeder ve okur.
"""
import json, os
from datetime import datetime, timedelta
from pathlib import Path

MONTHS_TR = {
    1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",
    7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık"
}

_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
REPORTS_DIR = _ROOT / "data" / "market_reports"


def _make_title(report_type: str, now: datetime) -> str:
    day   = now.day
    month = MONTHS_TR[now.month]
    year  = now.year
    if report_type == "daily":
        return f"{day} {month} {year} Global Piyasa Özeti — Dün Neler Oldu?"
    else:
        ws   = now - timedelta(days=6)
        wday = ws.day
        wmon = MONTHS_TR[ws.month]
        return f"{wday} {wmon} — {day} {month} {year} Haftası Global Piyasa Özeti — Bu Hafta Neler Oldu?"


def save_report(report_type: str, content: str) -> str:
    """Raporu diske kaydet. Dosya adını döner."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now       = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    filename  = f"{report_type}_{timestamp}.json"
    path = REPORTS_DIR / filename

    data = {
        "type":       report_type,
        "title":      _make_title(report_type, now),
        "content":    content,
        "created_at": now.isoformat(),
        "date":       now.strftime("%Y-%m-%d"),
        "date_label": f"{now.day} {MONTHS_TR[now.month]} {now.year}",
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"✓ Rapor kaydedildi: {filename}")
    return filename


def get_reports(report_type: str = None, limit: int = 10) -> list[dict]:
    """Diskteki raporları döner (en yeni önce)"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)

    results = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if report_type and data.get("type") != report_type:
                continue
            data["id"] = f.stem   # dosya adı = silme için ID
            results.append(data)
            if len(results) >= limit:
                break
        except Exception:
            pass
    return results


def get_latest(report_type: str) -> dict | None:
    """Belirtilen türün en son raporunu döner"""
    reports = get_reports(report_type, limit=1)
    return reports[0] if reports else None


def delete_by_id(report_id: str) -> bool:
    """Dosya adı (stem) ile raporu sil. Başarılı ise True döner."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for f in REPORTS_DIR.glob("*.json"):
        if f.stem == report_id:
            f.unlink()
            return True
    return False
