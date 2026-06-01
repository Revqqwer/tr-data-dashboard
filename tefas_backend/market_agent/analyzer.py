"""
Market Agent — Claude Analiz Motoru
Haberleri filtreler ve Türkçe rapor yazar.
"""
import json, logging
from datetime import datetime, timedelta
from anthropic import Anthropic

log = logging.getLogger(__name__)
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


# ── Prompt'lar ────────────────────────────────────────────────────────────────

_FILTER_PROMPT = """\
Sen bir finansal haber editörüsün. Aşağıdaki haber başlıklarını incele.
Tematik hisse yatırımı, sektör trendleri, önemli makro gelişmeler ve earnings açısından
EN ÖNEMLİ 15-20 haberi seç. Bloomberg ve WSJ kaynaklıları önceliklendir.

Seçim kriterleri:
- Tematik/sektörel yatırım fırsatları (AI, enerji geçişi, savunma, sağlık, altyapı, vb.)
- Önemli earnings sonuçları (büyük şirketler, S&P 500 bileşenleri)
- Makro etkili gelişmeler (Fed, enflasyon, büyüme, istihdam)
- Forex hareketleri (DXY, EUR/USD — kısa tutulabilir)
- Sektör/şirket birleşme-satın alma haberleri

{prev_context}

Seçilen her haber için JSON objesi:
{{"title":"...", "source":"...", "summary":"...", "theme":"macro|earnings|sector|forex|thematic|ma", "importance":1-5}}

Sadece JSON array döndür, açıklama ekleme."""

_DAILY_PROMPT = """\
Sen deneyimli bir finansal analistsin. Aşağıdaki verilere dayanarak Türkçe günlük piyasa bülteni yaz.

Hedef okuyucu: Tematik hisse yatırımı yapan, küresel piyasaları takip eden yatırımcı.
Ton: Profesyonel, net, gereksiz akademik dil yok. Doğrudan ve bilgilendirici.
Uzunluk: Tematik Fırsatlar bölümü hariç her bölüm kısa (2-4 cümle). Tematik bölüm gerekirse uzun olabilir.

FORMAT (kesinlikle bu yapıyı kullan, başka bir şey ekleme):

📊 GÜNLÜK PİYASA BÜLTENİ — {date}

━━━ PİYASALAR ━━━
[S&P 500, Nasdaq, DXY, 10Y — haberlerden çıkarabildiğin kadar kısa tablo/özet]

━━━ BUGÜNKÜ EARNINGS ━━━
[Her şirket: 🟢/🔴 ŞİRKET: beat/miss + tepki — yoksa "Bugün öne çıkan earnings yok"]

━━━ GÜNÜN KRİTİK HABERLERİ ━━━
1. Başlık — 2 cümle özet
2. Başlık — 2 cümle özet
3. Başlık — 2 cümle özet
4. Başlık — 2 cümle özet
5. Başlık — 2 cümle özet
[5-7 haber]

━━━ 🎯 TEMATİK FIRSATLAR ━━━
[En değerli bölüm. Hangi sektörler/temalar öne çıkıyor, katalitör nedir, hangi şirketler etkilenebilir?
Birden fazla tema varsa her birini ayrı paragrafta yaz. Spesifik olun.]

━━━ FOREX RADAR ━━━
[DXY ve önemli pariteler — 1-2 cümle]

━━━ ⚠️ RİSK RADARI ━━━
• Risk 1
• Risk 2
• Risk 3"""

_WEEKLY_PROMPT = """\
Sen deneyimli bir finansal analistsin. Türkçe haftalık piyasa bülteni yaz.

Ton: Profesyonel, analitik, tematik yatırımcıya yönelik.

FORMAT:

📋 HAFTALIK PİYASA BÜLTENİ — {date_range}

━━━ HAFTANIN ÖZETİ ━━━
[Haftanın 3-4 öne çıkan gelişmesi, genel piyasa seyri]

━━━ ÖNEMLİ EARNINGS SONUÇLARI ━━━
[Geçen haftanın dikkat çekici sonuçları]

━━━ ÖNÜMÜZDEKİ HAFTA EARNINGS ━━━
[Hangi büyük şirketler raporluyor? Beklentiler ne?]

━━━ MAKRO TAKVİM ━━━
[Fed toplantıları, CPI, istihdam, diğer önemli veriler]

━━━ 🏗️ TEMATİK DEEP-DIVE ━━━
[Haftanın öne çıkan yatırım teması — 3-5 paragraf detaylı analiz.
Sektör neden öne çıktı? Hangi şirketler etkilendi/etkilenecek?
Katalizörler, riskler, zaman ufku.]

━━━ ÖNÜMÜZDEKI HAFTA GÖRÜNÜMÜ ━━━
[Kısa değerlendirme — dikkat edilmesi gerekenler]"""


# ── Ana Fonksiyonlar ──────────────────────────────────────────────────────────

def filter_news(raw_news: list[dict], prev_report_content: str = "") -> list[dict]:
    """Claude Haiku ile önemli haberleri filtrele. Bir önceki rapor varsa tekrarları atlar."""
    if not raw_news:
        return []

    # Önceki rapor bağlamı
    if prev_report_content:
        # Önceki rapordan başlıkları çıkar (━━━ satırlarını ve bold başlıkları)
        import re
        prev_lines = [l.strip() for l in prev_report_content.split('\n')
                      if l.strip() and not l.startswith('━') and not l.startswith('📊')]
        prev_sample = '\n'.join(prev_lines[:30])
        prev_context = (
            "⚠️ ÖNEMLİ: Aşağıdaki haberler dün zaten rapor edildi. "
            "Bunları veya bunlara çok benzer haberleri SEÇME. "
            "Sadece YENİ gelişmeleri seç. "
            "Bir önceki haberin güncellemesi varsa 'GÜNCELLEME:' prefix'i ile dahil et.\n\n"
            f"DÜN RAPOR EDİLENLER (tekrarlama):\n{prev_sample}"
        )
    else:
        prev_context = ""

    headlines = "\n".join([
        f"[{i+1}] {item['title']} ({item['source']}) — {item.get('summary','')[:120]}"
        for i, item in enumerate(raw_news[:80])
    ])

    prompt = _FILTER_PROMPT.format(prev_context=prev_context)

    try:
        resp = _get_client().messages.create(
            model="claude-haiku-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt + "\n\nHABERLER:\n" + headlines}]
        )
        text = resp.content[0].text.strip()
        start = text.find('[')
        end   = text.rfind(']') + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
            log.info("Filtre: %d → %d haber", len(raw_news), len(result))
            return result
    except Exception as e:
        log.warning("Filtre hatası: %s", e)

    return raw_news[:15]   # fallback


def _format_earnings_text(earnings: list[dict]) -> str:
    if not earnings:
        return "Bugün öne çıkan earnings yok."
    lines = []
    for e in earnings[:20]:
        sym = e.get("symbol", "")
        actual   = e.get("eps_actual")
        estimate = e.get("eps_estimate")
        if actual is not None and estimate is not None:
            icon = "🟢 BEAT" if actual > estimate else "🔴 MISS"
            lines.append(f"{icon} {sym}: EPS {actual:.2f} vs tahmin {estimate:.2f}")
        else:
            lines.append(f"⏳ {sym}: Sonuç bekleniyor")
    return "\n".join(lines)


def _format_upcoming_earnings(earnings: list[dict]) -> str:
    if not earnings:
        return "Bilgi bulunamadı."
    lines = []
    for e in earnings[:30]:
        sym = e.get("symbol", "")
        dt  = e.get("date", "")
        est = e.get("epsEstimate", "?")
        hour = e.get("hour", "")
        time_label = " (açılış öncesi)" if hour == "bmo" else " (kapanış sonrası)" if hour == "amc" else ""
        lines.append(f"• {dt}{time_label} — {sym} (EPS tahmin: {est})")
    return "\n".join(lines)


def generate_daily_report(filtered_news: list[dict], earnings: list[dict]) -> str:
    today = datetime.now().strftime("%d %B %Y")

    news_text = "\n".join([
        f"- [{item.get('theme','?').upper()}|{item.get('importance',3)}★] "
        f"{item.get('title','')} — {item.get('summary','')[:200]}"
        for item in filtered_news
    ])

    content = (
        _DAILY_PROMPT.format(date=today)
        + "\n\nFİLTRELENMİŞ HABERLER:\n" + news_text
        + "\n\nEARNINGS:\n" + _format_earnings_text(earnings)
    )

    try:
        resp = _get_client().messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1800,
            messages=[{"role": "user", "content": content}]
        )
        return resp.content[0].text
    except Exception as e:
        log.error("Günlük rapor hatası: %s", e)
        return f"Rapor oluşturulamadı: {e}"


def generate_weekly_report(filtered_news: list[dict], upcoming_earnings: list[dict]) -> str:
    today      = datetime.now()
    week_start = (today - timedelta(days=7)).strftime("%d %b")
    week_end   = today.strftime("%d %b %Y")

    news_text = "\n".join([
        f"- [{item.get('theme','?').upper()}|{item.get('importance',3)}★] "
        f"{item.get('title','')} — {item.get('summary','')[:200]}"
        for item in filtered_news
    ])

    content = (
        _WEEKLY_PROMPT.format(date_range=f"{week_start} — {week_end}")
        + "\n\nHAFTANIN HABERLERİ:\n" + news_text
        + "\n\nÖNÜMÜZDEKİ HAFTA EARNINGS:\n" + _format_upcoming_earnings(upcoming_earnings)
    )

    try:
        resp = _get_client().messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": content}]
        )
        return resp.content[0].text
    except Exception as e:
        log.error("Haftalık rapor hatası: %s", e)
        return f"Rapor oluşturulamadı: {e}"
