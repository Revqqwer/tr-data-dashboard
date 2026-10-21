# -*- coding: utf-8 -*-
"""
Market Agent — Bülten Son Kontrolü

Model çıktısı kaydedilmeden önce:
  1. POZİSYON FIRSATLARI kutularındaki her kod TradingView'da (ABD borsaları) doğrulanır:
     - bulunamayan / artık işlem görmeyen / OTC kodlar,
     - ters (short/bear) ve kaldıraçlı (2x, 3x, 1.5x …) ETF'ler,
     - temayla alakasız şirketler (Haiku kontrolü: ör. "otomasyon" altında ABBV = AbbVie)
     metinden çıkarılır.
  2. Geleceğe dönük cümlelerde yanlış yıl (ör. Eylül 2026'da "Fed 2025'te artıracak") düzeltilir.
  3. Latin dışı yazı (Çince/Japonca/Korece karakter sızıntısı) temizlenir.
"""
import json, logging, re
from datetime import datetime

import requests

from tefas_backend.market_agent.positions import _STOP, _split_label

log = logging.getLogger(__name__)

_TV_HEADERS = {"Origin": "https://www.tradingview.com", "User-Agent": "Mozilla/5.0"}
_OK_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSE ARCA", "CBOE", "BATS"}
# Ters / kaldıraçlı ETF tanımları (TradingView açıklamasından)
_LEVERAGED_RE = re.compile(
    r"(\binverse\b|\bshort\b|\bbear\b|\bultra(short|pro)?\b|\b\d+(\.\d+)?x\b|-\d+x|leveraged|daily .*\bbull\b)",
    re.I)
_FOREIGN_SCRIPT_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯Ѐ-ӿ]+")


# ── 1. Kod doğrulama ──────────────────────────────────────────────────────────

# Noktalı sınıf kodlarını da yakala (BRK.B, RDS.A); yabancı borsa ekli olanlar (AIR.PA) ABD'de bulunamaz → çıkar
_TK_RE = re.compile(r"(?<![\w.&\-])[A-Z]{1,5}(?:\.[A-Z]{1,2})?(?![\w&])")
_TICKER_LIST_RE = re.compile(r"^[A-Z0-9.,/&\- ]+$")


def _strip_descriptions(body: str) -> str:
    """'XOM (Exxon - Trinidad)' → 'XOM'; ama '(SMH, SOXX)' gibi kod listesi olan parantezi korur."""
    return re.sub(r"\(([^)]*)\)", lambda m: f" {m.group(1)} " if _TICKER_LIST_RE.match(m.group(1).strip() or "x")
                  else " ", body)


def _extract_tickers(body: str) -> list[str]:
    out = []
    for tk in _TK_RE.findall(_strip_descriptions(body)):
        if tk in _STOP or tk.split(".")[0] in _STOP:
            continue
        if tk not in out:
            out.append(tk)
    return out


def _tv_lookup(tickers: list[str]) -> dict:
    """{ticker: {'desc','type','subtype','exchange'}} — ABD borsalarında bulunanlar."""
    if not tickers:
        return {}
    try:
        r = requests.post("https://scanner.tradingview.com/america/scan", headers=_TV_HEADERS, timeout=20,
                          json={"filter": [{"left": "name", "operation": "in_range", "right": tickers}],
                                "columns": ["name", "description", "type", "subtype", "exchange", "is_primary"],
                                "range": [0, 500]})
        rows = r.json().get("data", [])
    except Exception as e:
        log.warning("TradingView kod doğrulaması yapılamadı: %s", e)
        return None   # None = kontrol yapılamadı, hiçbir şey silme
    out = {}
    for x in rows:
        name, desc, typ, sub, exch, primary = x["d"]
        # aynı kod birden çok borsada olabilir: birincil/onaylı borsadakini tercih et
        if name in out and out[name]["exchange"] in _OK_EXCHANGES and not primary:
            continue
        out[name] = {"desc": desc or "", "type": typ, "subtype": sub, "exchange": exch}
    return out


def _theme_blocks(content: str) -> list[dict]:
    """TEMATİK bölümlerindeki temalar: [{'theme', 'tickers'}] (kutudaki tüm satırlar dahil)."""
    themes, cur, in_sec = [], None, False
    for line in content.split("\n"):
        t = line.strip()
        sec = re.match(r"^━+\s*(.*?)\s*━+$", t)
        if sec:
            in_sec = "TEMATİK" in sec.group(1).upper()
            cur = None
            continue
        if not in_sec:
            continue
        bold = re.match(r"^\*\*(.+?)\*\*$", t)
        if bold:
            cur = {"theme": bold.group(1), "tickers": []}
            themes.append(cur)
        elif cur and t.startswith("│"):
            _, body = _split_label(t.lstrip("│").strip())
            for tk in _extract_tickers(body):
                if tk not in cur["tickers"]:
                    cur["tickers"].append(tk)
    return themes


def _theme_mismatches(themes: list[dict], info: dict) -> dict:
    """Haiku ile 'kod gerçekten bu temaya mı ait?' kontrolü. Döner: {ticker: neden}."""
    lines = []
    for th in themes:
        items = [f"{tk} = {info[tk]['desc']}" for tk in th["tickers"] if tk in info]
        if items:
            lines.append(f"TEMA: {th['theme']}\n  " + "\n  ".join(items))
    if not lines:
        return {}
    prompt = (
        "Bir finans bülteninde her temanın altında hisse/ETF kodları listelenmiş. Kodun yanında borsadaki "
        "GERÇEK şirket/ETF adı var. Yalnızca temayla AÇIKÇA alakasız olanları işaretle (ör. 'otomasyon' "
        "temasında bir ilaç şirketi, 'enerji altyapısı' temasında bir restoran yazılımı, 'madencilik' altında "
        "bir tarım ETF'i). Dolaylı ama makul bağlantıları (tedarikçi, müşteri, sektör ETF'i) İŞARETLEME.\n\n"
        + "\n\n".join(lines)
        + '\n\nSadece JSON döndür: {"remove": [{"ticker": "...", "reason": "kısa Türkçe neden"}]}'
    )
    try:
        from tefas_backend.market_agent.analyzer import _get_client, _text, _log_usage
        resp = _get_client().messages.create(
            model="claude-haiku-5-5", max_tokens=4000, output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}])
        _log_usage(resp, "claude-haiku-5-5", "kod-tema kontrolü")
        txt = _text(resp)
        data = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        return {x["ticker"]: x.get("reason", "temayla alakasız") for x in data.get("remove", [])
                if x.get("ticker") in info}
    except Exception as e:
        log.warning("Kod-tema kontrolü yapılamadı: %s", e)
        return {}


def _drop_tickers_from_line(raw: str, bad: set) -> str | None:
    """Satırdan kötü kodları (ve hemen ardındaki parantez açıklamasını) çıkarır. Kod kalmazsa None."""
    label, body = _split_label(raw)
    for tk in bad:
        body = re.sub(r"(?<![\w.&\-])" + re.escape(tk) + r"(?![\w.&])(\s*\([^)]*\))?", "", body)
    # temizlik: boş virgüller, boş parantezler, baştaki/sondaki ayraçlar
    body = re.sub(r"\(\s*[,/]?\s*\)", "", body)
    body = re.sub(r"\(\s*[,/]\s*", "(", body)
    body = re.sub(r"\s*[,/]\s*\)", ")", body)
    body = re.sub(r"(\s*,\s*){2,}", ", ", body)
    body = re.sub(r"\s*/\s*/\s*", " / ", body)
    body = body.strip().strip(",/").strip()
    if not _extract_tickers(body):
        return None
    return (f"{label}: {body.strip()}" if label else body)


def clean_positions(content: str) -> tuple[str, list[str]]:
    themes = _theme_blocks(content)
    tickers = sorted({tk for th in themes for tk in th["tickers"]})
    if not tickers:
        return content, []
    info = _tv_lookup(tickers)
    if info is None:          # TradingView'a ulaşılamadı → dokunma
        return content, []

    bad, notes = set(), []
    for tk in tickers:
        i = info.get(tk)
        if not i:
            bad.add(tk); notes.append(f"{tk}: ABD borsalarında bulunamadı (kapanmış/yanlış kod)")
        elif i["exchange"] not in _OK_EXCHANGES:
            bad.add(tk); notes.append(f"{tk}: {i['exchange']} (tezgâhüstü) — çıkarıldı")
        elif i["type"] == "fund" and _LEVERAGED_RE.search(i["desc"]):
            bad.add(tk); notes.append(f"{tk}: ters/kaldıraçlı ETF ({i['desc']})")
    good = {tk: info[tk] for tk in tickers if tk not in bad}
    for tk, why in _theme_mismatches(themes, good).items():
        bad.add(tk); notes.append(f"{tk}: temayla alakasız — {why} ({good[tk]['desc']})")
    if not bad:
        return content, []

    out, in_sec, box = [], False, []
    for line in content.split("\n"):
        t = line.strip()
        sec = re.match(r"^━+\s*(.*?)\s*━+$", t)
        if sec:
            in_sec = "TEMATİK" in sec.group(1).upper()
        if in_sec and t.startswith("┌"):
            box = [line]
            continue
        if box:
            if t.startswith("└"):
                rows = [l for l in box[1:] if l.strip().startswith("│")]
                if rows:                       # en az bir kod satırı kaldıysa kutuyu yaz
                    out.extend(box + [line])
                box = []
                continue
            if t.startswith("│"):
                raw = t.lstrip("│").strip()
                new = _drop_tickers_from_line(raw, bad)
                if new is not None:
                    box.append("│ " + new)
            else:
                box.append(line)
            continue
        out.append(line)
    if box:                                    # kapanmamış kutu — olduğu gibi bırak
        out.extend(box)
    return "\n".join(out), notes


# ── 2. Yıl düzeltmesi ─────────────────────────────────────────────────────────

_FUTURE_RE = re.compile(r"(ecek|acak|ebilir|abilir|bekleniyor|beklenen|beklenti|tahmin|öngör|hedef|"
                        r"dominant|teması|planlı|yapacak|olacak)", re.I)


def fix_years(content: str, year: int | None = None) -> tuple[str, list[str]]:
    """İçinde bulunulan yıldan önceki yılın geleceğe dönük cümlelerde kullanılmasını düzeltir
    (ör. 2026'da 'Fed 2025'te üç artırım yapabilir' → 2026). Geçmişi anlatan cümlelere dokunmaz."""
    year = year or datetime.now().year
    prev = str(year - 1)
    notes = []

    def fix_sentence(s: str) -> str:
        if prev not in s or not _FUTURE_RE.search(s) or str(year) in s:
            return s          # cümlede bu yıl da geçiyorsa karşılaştırmadır (2025'te X, 2026'da Y)
        if re.search(r"(geçen yıl|geçtiğimiz yıl|\b" + prev + r"(\s|')?(yılı(nda)?\s)?(ile|'e göre|e göre|'den|den)\b|"
                     r"\b" + prev + r"\s*(sonu|başı|seviyes))", s, re.I):
            return s          # açıkça geçmişe atıf — dokunma
        notes.append(s.strip()[:120])
        return s.replace(prev, str(year))

    parts = re.split(r"(?<=[.!?])(\s+)", content)
    return "".join(fix_sentence(p) for p in parts), notes


# ── 3. Yabancı yazı sızıntısı ─────────────────────────────────────────────────

def strip_foreign_scripts(content: str) -> tuple[str, list[str]]:
    found = _FOREIGN_SCRIPT_RE.findall(content)
    if not found:
        return content, []
    return _FOREIGN_SCRIPT_RE.sub("", content), found


def validate_report(content: str) -> str:
    content, foreign = strip_foreign_scripts(content)
    content, years = fix_years(content)
    content, removed = clean_positions(content)
    for f in foreign:
        log.warning("🧹 Yabancı karakter silindi: %s", f)
    for y in years:
        log.warning("🧹 Yıl düzeltildi: %s", y)
    for r in removed:
        log.warning("🧹 Kod çıkarıldı — %s", r)
    if foreign or years or removed:
        print(f"🧹 Son kontrol: {len(removed)} kod çıkarıldı, {len(years)} yıl düzeltildi, "
              f"{len(foreign)} yabancı karakter silindi")
        for r in removed:
            print("   -", r)
    return content
