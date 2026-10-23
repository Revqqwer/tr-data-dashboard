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
        if now.weekday() == 0:   # Pazartesi: son seans Cuma
            return f"{day} {month} {year} Global Piyasa Özeti — Hafta Sonu ve Haftanın Gündemi"
        return f"{day} {month} {year} Global Piyasa Özeti — Dün Neler Oldu?"
    else:
        ws   = now - timedelta(days=6)
        wday = ws.day
        wmon = MONTHS_TR[ws.month]
        return f"{wday} {wmon} — {day} {month} {year} Haftası Global Piyasa Özeti — Bu Hafta Neler Oldu?"


def save_report(report_type: str, content: str, meta: dict | None = None) -> str:
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
    if meta:
        data.update(meta)   # ör. news_titles: bültende kullanılan haberlerin orijinal başlıkları (tekrar önleme)
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


_EN_MONTHS = {"January": "Ocak", "February": "Şubat", "March": "Mart", "April": "Nisan", "May": "Mayıs",
              "June": "Haziran", "July": "Temmuz", "August": "Ağustos", "September": "Eylül",
              "October": "Ekim", "November": "Kasım", "December": "Aralık"}


def get_by_date(date: str, report_type: str = None) -> dict | None:
    """O günün raporu (YYYY-MM-DD). Tür verilmezse önce günlük, yoksa haftalık."""
    for t in ([report_type] if report_type else ["daily", "weekly"]):
        for f in sorted(REPORTS_DIR.glob(f"{t}_{date.replace('-', '')}_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["id"] = f.stem
                return data
            except Exception:
                pass
    return None


def web_html(content: str) -> str:
    """Bülten metnini herkese açık web sayfası için güvenli HTML'e çevirir
    (━━━ bölüm başlıkları → h2, ┌│└ kutuları → kart, **kalın** → <b>)."""
    import html, re
    out, box = [], None

    def inline(s: str) -> str:
        s = html.escape(s)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s).replace("**", "")

    for i, line in enumerate((content or "").split("\n")):
        t = line.strip()
        if i == 0 and (t.startswith("📊") or t.startswith("📋") or t.startswith("# ")):
            continue                                   # başlık sayfada ayrıca yazıyor
        if t.startswith("```") or re.fullmatch(r"[─━\-]{3,}", t):
            continue
        m = re.match(r"^━+\s*(.*?)\s*━+$", t)
        if m:
            out.append(f"<h2>{inline(m.group(1))}</h2>")
            continue
        if t.startswith("┌"):
            box = []
            continue
        if t.startswith("└"):
            if box:
                out.append('<div class="box"><div class="box-t">📌 Pozisyon fırsatları</div>' +
                           "".join(f"<div>{inline(x)}</div>" for x in box) + "</div>")
            box = None
            continue
        if t.startswith("│"):
            t = t.strip("│").strip()
            if t and box is not None:
                box.append(t)
            continue
        if not t:
            continue
        hm = re.match(r"^#{1,6}\s*(.*)$", t)
        if hm:
            out.append(f"<h3>{inline(hm.group(1))}</h3>")
        elif re.match(r"^[•\-–▸●▪]\s+", t):
            item = re.sub(r"^[•\-–▸●▪]\s+", "", t)
            out.append(f'<p class="li">{inline(item)}</p>')
        else:
            out.append(f"<p>{inline(t)}</p>")
    text = "\n".join(out)
    for en, tr in _EN_MONTHS.items():
        text = re.sub(rf"(\d{{1,2}}) {en} (\d{{4}})", rf"\1 {tr} \2", text)
    return text


def app_plain(content: str) -> str:
    """Mobil uygulama bülteni düz metin (white-space: pre-wrap) gösterir: markdown işaretleri ve kutu çizgileri
    telefonda bozuk görünür. Bu fonksiyon metni dar ekranda okunur sade metne çevirir."""
    import re
    out = []
    lines = (content or "").split("\n")
    for i, line in enumerate(lines):
        t = line.strip()
        if i == 0 and (t.startswith("📊") or t.startswith("📋") or t.startswith("# ")):
            continue                                   # başlık zaten kartta yazıyor
        if t.startswith("```"):
            continue
        m = re.match(r"^━+\s*(.*?)\s*━+$", t)
        if m:                                          # bölüm başlığı
            if out and out[-1] != "":
                out.append("")
            out.append("▍" + m.group(1).upper())
            continue
        if t.startswith("┌"):
            out.append("📌 Pozisyon fırsatları:")
            continue
        if t.startswith("└") or re.fullmatch(r"[─━\-]{3,}", t):
            continue
        if t.startswith("│"):
            t = t.lstrip("│").strip()
            if not t:
                continue
            line = "   " + t
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = line.replace("**", "").replace("__", "")
        out.append(line.rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for en, tr in _EN_MONTHS.items():
        text = re.sub(rf"(\d{{1,2}}) {en} (\d{{4}})", rf"\1 {tr} \2", text)
    return text
