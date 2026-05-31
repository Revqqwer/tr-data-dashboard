"""
Market Agent — Rapor Depolama
JSON dosyalarına kaydeder ve okur.
"""
import json, os
from datetime import datetime
from pathlib import Path

_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
REPORTS_DIR = _ROOT / "data" / "market_reports"


def save_report(report_type: str, content: str) -> str:
    """Raporu diske kaydet. Dosya adını döner."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = f"{report_type}_{timestamp}.json"
    path = REPORTS_DIR / filename

    data = {
        "type":       report_type,
        "content":    content,
        "created_at": datetime.now().isoformat(),
        "date":       datetime.now().strftime("%Y-%m-%d"),
        "date_label": datetime.now().strftime("%d %B %Y"),
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
