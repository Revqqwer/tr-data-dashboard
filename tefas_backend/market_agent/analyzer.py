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


# Model fiyatları ($ / 1M token) — girdi, çıktı
_PRICES = {
    "claude-haiku-5-5":  (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5":  (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-8":   (5.0, 25.0),
}


def _text(resp) -> str:
    """Yanıttaki metin bloklarını birleştirir (5.5 modelleri önce düşünme bloğu döndürür)."""
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError(f"Model yanıtı reddetti: {getattr(resp, 'stop_details', None)}")
    return "".join(b.text for b in resp.content if b.type == "text")


def _log_usage(resp, model: str, label: str):
    """API çağrısının token kullanımını ve tahmini maliyetini logla."""
    try:
        u = resp.usage
        pin  = getattr(u, 'input_tokens', 0) or 0
        pout = getattr(u, 'output_tokens', 0) or 0
        cin, cout = _PRICES.get(model, (0.0, 0.0))
        cost = pin / 1e6 * cin + pout / 1e6 * cout
        log.info("💰 [%s] %s | girdi=%d çıktı=%d token | ≈ $%.4f (₺%.2f)",
                 label, model, pin, pout, cost, cost * 41)  # ~41 TL/USD
    except Exception as e:
        log.warning("Usage loglanamadı (%s): %s", label, e)


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
Uzunluk: KISA tut — bülten telefonda okunuyor. Toplam en fazla ~4.000 karakter. Her bölüm kısa (2-4 cümle).

FORMAT (kesinlikle bu yapıyı kullan, başka bir şey ekleme):

📊 GÜNLÜK PİYASA BÜLTENİ — {date}

━━━ PİYASALAR ━━━
[Sözel, akıcı bir paragraf yaz (4-6 cümle); endeks listesi, tablo ya da madde DÖKME.
Önce günün genel havasını söyle (ör. risk-off / risk-on, nerede satış, nerede alım vardı).
Bölge bölge kısaca anlat ama yalnızca ÖNEMLİ endeksleri an: ABD için S&P 500 ve Nasdaq,
Avrupa için genel seyir + gerekirse DAX, Asya için genel seyir + yalnızca sert hareket eden endeks(ler),
Türkiye için BIST 100; dolar/faiz/altın/petrolde sadece dikkat çeken bir hareket varsa değin.
Örnek ton: "Gün risk-off geçti. ABD'de teknoloji ağırlıklı Nasdaq %2 düşerken Avrupa'da Almanya
endeksi hafif yükseldi. En sert hareket Asya'daydı: KOSPI %7,85 geriledi."
Rakam az olsun: yalnızca öne çıkan hareketler için yüzde ver, endeks seviyelerini (puan) yazma.
Rakamları SADECE aşağıdaki PİYASA VERİLERİ'nden al; uydurma, haberlerden rakam çıkarma.
Asya endeksleri bülten saatinde işlem görüyor olabilir; onları "bu sabah" diye an.]

━━━ BUGÜNKÜ EARNINGS ━━━
[En fazla 5 şirket, her biri tek satır: 🟢/🔴 ŞİRKET: beat/miss + tepki — yoksa "Bugün öne çıkan earnings yok"]

━━━ GÜNÜN KRİTİK HABERLERİ ━━━
1. Başlık — 2 cümle özet
2. Başlık — 2 cümle özet
3. Başlık — 2 cümle özet
4. Başlık — 2 cümle özet
5. Başlık — 2 cümle özet
[En fazla 5 haber. Yalnızca YENİ gelişmeler — SON BÜLTENLER'de anlatılanları tekrar etme.]

━━━ 🇹🇷 TÜRKİYE ━━━
[TÜRKİYE HABERLERİ listesinden yalnızca Türkiye ekonomisi, TCMB, enflasyon, Borsa İstanbul,
Türk şirketleri ve Türkiye'yi doğrudan ilgilendiren gelişmeleri seç; global haberleri burada tekrar etme.
3-5 madde, her biri 1-2 cümle, kendi cümlelerinle yaz (metni kopyalama). Uygun haber yoksa
"Bugün öne çıkan Türkiye haberi yok" yaz.]

━━━ 🎯 TEMATİK FIRSATLAR ━━━
[EN FAZLA 3 tema (yeni gelişme azsa 1-2 yeterli). Her tema ayrı blok, ŞU FORMAT:

**🏷️ TEMA ADI — Kısa başlık**
[2 cümle analiz: katalitör nedir, neden önemli, zaman ufku]

┌─ POZİSYON FIRSATLARI ─────────────────────────────────
│ Ticker1, Ticker2, Ticker3
│ Sektör/ETF: ETF1, ETF2
└───────────────────────────────────────────────────────

Risk: tek satır

KOD KURALLARI: Yalnızca NYSE/Nasdaq'ta BUGÜN işlem gören hisse ve ETF kodlarını yaz (en fazla 4 hisse + 2 ETF).
Kodun hangi şirkete ait olduğundan emin değilsen YAZMA. Satın alınmış/birleşmiş/iflas etmiş şirketlerin eski
kodlarını, tezgâhüstü (OTC) ADR'leri ve yabancı borsa kodlarını (ör. .PA, .DE, .KS) yazma. Ters (short/bear/inverse)
ve kaldıraçlı (2x, 3x, Ultra) ETF'leri ASLA yazma. "Sektör/ETF:" satırına yalnızca ETF kodu yaz, açıklama yazma.]

━━━ FOREX RADAR ━━━
[DXY ve önemli pariteler — 1-2 cümle]

━━━ ⚠️ RİSK RADARI ━━━
• Risk 1
• Risk 2
• Risk 3"""

_WEEKLY_PROMPT = """\
Sen deneyimli bir finansal analistsin. Türkçe haftalık piyasa bülteni yaz.

Ton: Profesyonel, analitik, tematik yatırımcıya yönelik.
Uzunluk: Her bölüm kısa-orta (3-5 cümle). Tematik deep-dive gerekirse uzun olabilir.

FORMAT (kesinlikle bu yapıyı kullan):

📋 HAFTALIK PİYASA BÜLTENİ — {date_range}

━━━ HAFTANIN ÖZETİ ━━━
[Haftanın 3-4 öne çıkan gelişmesi, genel piyasa seyri — 3-4 cümle]

━━━ ÖNEMLİ EARNINGS SONUÇLARI ━━━
[Her şirket için: 🟢/🔴 ŞİRKET: beat/miss + kısa yorum. Yoksa "Öne çıkan earnings yok."]

━━━ ÖNÜMÜZDEKİ HAFTA EARNINGS ━━━
[Hangi büyük şirketler raporluyor? Her biri için: • TARIH — SİRKET (EPS tahmin: X)]

━━━ MAKRO TAKVİM ━━━
[Fed, CPI, istihdam, diğer önemli veriler — bullet liste]
• Tarih — Veri/Etkinlik

━━━ 🇹🇷 TÜRKİYE'DE BU HAFTA ━━━
[TÜRKİYE HABERLERİ listesinden haftanın Türkiye'yi ilgilendiren en önemli 4-6 gelişmesi
(TCMB, enflasyon, Borsa İstanbul, Türk şirketleri, politika/ekonomi). Her biri 1-2 cümle,
kendi cümlelerinle yaz (metni kopyalama). Global haberleri burada tekrar etme.]

━━━ 🏗️ TEMATİK DEEP-DIVE ━━━
[Haftanın öne çıkan yatırım teması/temaları. Her tema için ŞU FORMAT:

**🏷️ TEMA ADI — Kısa başlık**
[2-3 cümle analiz: katalitör nedir, neden önemli, zaman ufku]

┌─ POZİSYON FIRSATLARI ─────────────────────────────────
│ Ticker1, Ticker2, Ticker3
│ Sektör/ETF: SEKTÖR_ADI
└───────────────────────────────────────────────────────

[Risk faktörleri varsa 1 satırda]
]

━━━ ⚠️ RİSK RADARI ━━━
• Risk 1
• Risk 2
• Risk 3

━━━ ÖNÜMÜZDEKI HAFTA GÖRÜNÜMÜ ━━━
[Kısa değerlendirme — dikkat edilmesi gerekenler, kritik seviyeler]"""


# ── Ana Fonksiyonlar ──────────────────────────────────────────────────────────

def filter_news(raw_news: list[dict], prev_report_content: str = "") -> list[dict]:
    """Claude Haiku ile önemli haberleri filtrele. Bir önceki rapor varsa tekrarları atlar."""
    if not raw_news:
        return []

    # Son bültenlerde anlatılanlar (tekrar önleme) — çağıran, recent_context() özetini verir
    if prev_report_content:
        prev_context = (
            "⚠️ ÖNEMLİ: Aşağıdaki konular SON BÜLTENLERDE zaten anlatıldı. Aynı olayı/açıklamayı/tahmini "
            "(farklı kaynaktan ya da farklı başlıkla gelse bile) SEÇME. Yalnızca YENİ gelişmeleri seç; "
            "anlatılmış bir konuda gerçekten yeni bir gelişme (karar, rakam, sonuç) varsa başlığına "
            "'GÜNCELLEME:' ekleyerek seçebilirsin.\n\n"
            f"SON BÜLTENLERDE ANLATILANLAR (tekrarlama):\n{prev_report_content}"
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
            model="claude-haiku-5-5",
            max_tokens=8000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt + "\n\nHABERLER:\n" + headlines}]
        )
        _log_usage(resp, "claude-haiku-5-5", "haber filtreleme")
        text = _text(resp).strip()
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


def _format_tr_news(items: list[dict] | None) -> str:
    if not items:
        return "(Türkiye haberi alınamadı)"
    return "\n".join(f"- {x['title']} — {x.get('summary','')[:200]}" for x in items)


def _format_markets(markets: list[dict] | None) -> str:
    if not markets:
        return "(piyasa verisi alınamadı — PİYASALAR bölümünde rakam verme, sadece genel havayı yaz)"
    lines, group = [], None
    for m in markets:
        if m["group"] != group:
            group = m["group"]
            lines.append(f"[{group}]")
        ch = f"{m['change']:+.2f}%" if m.get("change") is not None else "?"
        wk = f"{m['week']:+.2f}%" if m.get("week") is not None else "?"
        dt = ''
        if m.get('date'):
            d = datetime.strptime(m['date'], '%Y-%m-%d')
            dt = f" | seans tarihi {['Pzt','Sal','Çar','Per','Cum','Cmt','Paz'][d.weekday()]} {d:%d.%m}"
        lines.append(f"- {m['name']}: {m['price']:,.2f} | günlük {ch} | haftalık {wk}{dt}")
    return "\n".join(lines)


def _time_context() -> str:
    """Bültenin hangi güne ve hangi seansa ait olduğunu modele açıkça söyler (Pazartesi = Cuma kapanışı)."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('Europe/Istanbul'))
    days = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
    wd = now.weekday()
    if wd == 0:
        last = now - timedelta(days=3)
        return (f"ZAMAN BAĞLAMI: Bugün Pazartesi ({now:%d.%m.%Y}), bülten sabah 06:00'da yazılıyor. "
                f"Hafta sonu piyasalar kapalıydı. ABD, Avrupa ve Borsa İstanbul'un SON SEANSI CUMA ({last:%d.%m.%Y}) idi: "
                "PİYASA VERİLERİ'ndeki ABD, Avrupa, Türkiye ve döviz/emtia rakamları CUMA KAPANIŞIDIR. "
                "Bunları 'dün' ya da 'bugün' diye DEĞİL, 'Cuma günü' / 'haftanın son işlem gününde' diye anlat. "
                "Asya endeksleri ise bu sabah (Pazartesi) işlem görüyor; onları 'bu sabah Asya'da' diye an. "
                "Haberlerden Cuma'ya ait olanları 'Cuma günü', hafta sonuna ait olanları 'hafta sonu' diye ayır; "
                "Pazartesi günü yaşanmış gibi yazma. Bülten başlığında da Cuma ve hafta sonunu özetlediğini belli et.")
    if wd == 5:
        return (f"ZAMAN BAĞLAMI: Bugün Cumartesi ({now:%d.%m.%Y}). Son seans dün, Cuma'ydı; ABD/Avrupa/Türkiye rakamları "
                "Cuma kapanışı. Asya piyasaları bugün kapalı; Asya rakamları da Cuma kapanışıdır, 'bu sabah' deme.")
    yday = days[wd - 1]
    return (f"ZAMAN BAĞLAMI: Bugün {days[wd]} ({now:%d.%m.%Y}). ABD, Avrupa ve Türkiye rakamları dünkü ({yday}) kapanış; "
            "Asya endeksleri bu sabah işlem görüyor.")


def recent_context(limit: int = 3) -> str:
    """Son günlük bültenlerin özeti (tarih, haber başlıkları, temalar ve kodlar) — tekrar ve çelişki önleme."""
    import re
    from tefas_backend.market_agent.reports import get_reports
    from tefas_backend.market_agent.positions import parse_report_positions
    days = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz']
    blocks = []
    for r in get_reports("daily", limit):
        c = r.get("content", "")
        sec = c.split("GÜNÜN KRİTİK HABERLERİ")[-1].split("━━━")[1] if "GÜNÜN KRİTİK HABERLERİ" in c else ""
        heads = []
        for line in sec.split("\n"):
            m = re.match(r"^\s*\d+\.\s*(.+)$", line)
            if m:
                h = m.group(1).replace("**", "")
                heads.append(h.split(" — ")[0].strip()[:140])
        tr = c.split("🇹🇷 TÜRKİYE")[-1].split("━━━")[1] if "🇹🇷 TÜRKİYE" in c else ""
        tr_heads = [l.strip("•- ").strip()[:110] for l in tr.split("\n") if l.strip().startswith("•")]
        themes = [f"{t['theme']} [{', '.join(t['tickers'][:6])}]" for t in parse_report_positions(c)]
        try:
            d = datetime.strptime(r.get("date", ""), "%Y-%m-%d")
            label = f"{days[d.weekday()]} {d:%d.%m}"
        except Exception:
            label = r.get("date", "?")
        blocks.append(f"[{label} bülteni]\nHaberler: " + " | ".join(heads)
                      + ("\nTürkiye: " + " | ".join(tr_heads) if tr_heads else "")
                      + ("\nTemalar ve kodlar: " + " | ".join(themes) if themes else ""))
    return "\n\n".join(blocks)


def _recent_rules(recent: str) -> str:
    if not recent:
        return ""
    return ("\n\nSON BÜLTENLER (okur bunları zaten okudu):\n" + recent + "\n\n"
            "TEKRAR VE TUTARLILIK KURALLARI:\n"
            "- Son bültenlerde anlatılan haberleri, temaları ve Türkiye maddelerini TEKRAR ETME. Aynı konuda gerçekten "
            "yeni bir gelişme (karar, rakam, sonuç) varsa tek cümleyle 'güncelleme' olarak yaz.\n"
            "- Son bültenlerdeki bilgilerle ÇELİŞME: bir şirketin halka açık olup olmadığı, kişilerin unvanı "
            "(ör. kim Fed başkanı, kim Hazine bakanı), gerçekleşmiş bir olayın durumu aynı kalmalı.\n"
            "- Aynı hisse/ETF için son bültenlerdekinin tersi bir görüş veriyorsan (ör. önce 'al' sonra 'kaçın') "
            "bunu açıkça belirt ve nedenini yaz; sebepsiz yön değiştirme.")


def _monday_rules(week_earnings: list[dict] | None) -> str:
    from zoneinfo import ZoneInfo
    if datetime.now(ZoneInfo('Europe/Istanbul')).weekday() != 0:
        return ""
    return ("\n\nPAZARTESİ KURALLARI (önemli):\n"
            "- Cuma kapanışı ve Cuma'nın haberleri CUMARTESİ bülteninde verildi (SON BÜLTENLER'de görüyorsun). "
            "Onları yeniden anlatma.\n"
            "- PİYASALAR: Cuma kapanışını en fazla bir cümleyle hatırlat; asıl olarak haftaya nasıl başlandığını "
            "anlat: bu sabah Asya ve 'ABD vadeli (bu sabah)' verileri.\n"
            "- 'BUGÜNKÜ EARNINGS' bölümünün adını 'BU HAFTA BİLANÇOLAR' yap ve BU HAFTANIN BİLANÇO TAKVİMİ "
            "listesinden en önemli 5-8 büyük şirketi gün adıyla yaz. Listede olmayan şirket ya da tarih yazma.\n"
            "- GÜNÜN KRİTİK HABERLERİ: yalnızca hafta sonu (Cumartesi 06:00'dan sonra) çıkan yeni gelişmeler. "
            "Önemli yeni haber yoksa bölümün adını 'HAFTANIN GÜNDEMİ' yap ve 3-5 maddede bu hafta piyasayı neyin "
            "beklediğini yaz (bilançolar, haberlerde geçen bu haftaki toplantı/veri/olaylar). Haberlerde tarihi "
            "geçmeyen veri/toplantı tarihi UYDURMA.\n"
            "- TEMATİK FIRSATLAR: yeni gelişme yoksa en fazla 1-2 tema; Cumartesi'nin temalarını tekrarlama.\n\n"
            "BU HAFTANIN BİLANÇO TAKVİMİ (Finnhub):\n" + _format_upcoming_earnings(week_earnings or []))


def _create(model: str, content: str, label: str, max_tokens: int = 16000):
    """Model çağrısı; yanıt token sınırında kesilirse (yarım bülten) bir kez daha dener."""
    for attempt in range(2):
        resp = _get_client().messages.create(model=model, max_tokens=max_tokens,
                                             messages=[{"role": "user", "content": content}])
        _log_usage(resp, model, label)
        if getattr(resp, "stop_reason", None) != "max_tokens":
            return _text(resp)
        log.warning("%s yarıda kesildi (max_tokens) — yeniden deneniyor", label)
    return _text(resp)


def generate_daily_report(filtered_news: list[dict], earnings: list[dict], markets: list[dict] | None = None,
                          tr_news: list[dict] | None = None, recent: str = "",
                          week_earnings: list[dict] | None = None) -> str:
    today = datetime.now().strftime("%d %B %Y")

    news_text = "\n".join([
        f"- [{item.get('theme','?').upper()}|{item.get('importance',3)}★] "
        f"{item.get('title','')} — {item.get('summary','')[:200]}"
        for item in filtered_news
    ])

    content = (
        _DAILY_PROMPT.format(date=today)
        + "\n\n" + _time_context()
        + "\nHer piyasa rakamının yanında TradingView'daki SEANS TARİHİ var; esas bu tarihtir. Seans tarihi bugün değilse "
          "o rakamı o günün hareketi olarak anlat (ör. 'Cuma günü S&P 500 %1 düştü'); tatil yüzünden eski kalan bir piyasa "
          "varsa 'tatil nedeniyle kapalıydı' diye belirt, bugün olmuş gibi yazma."
        + "\n\nPİYASA VERİLERİ (TradingView, kesin veri — PİYASALAR bölümünde yalnızca bunları kullan):\n"
        + _format_markets(markets)
        + "\n\nFİLTRELENMİŞ HABERLER:\n" + news_text
        + "\n\nTÜRKİYE HABERLERİ (Bloomberg HT başlıkları — yalnızca 🇹🇷 TÜRKİYE bölümü için):\n" + _format_tr_news(tr_news)
        + "\n\nEARNINGS:\n" + _format_earnings_text(earnings)
        + _recent_rules(recent)
        + _monday_rules(week_earnings)
        + f"\n\nYIL: İçinde bulunduğumuz yıl {datetime.now().year}. Bu yıla ve geleceğe ait olayları "
          f"{datetime.now().year - 1} diye yazma; '{datetime.now().year - 1}' yalnızca geçen yıla açık atıf için kullanılır."
    )

    try:
        return _create("claude-sonnet-5-5", content, "günlük rapor")
    except Exception as e:
        log.error("Günlük rapor hatası: %s", e)
        return f"Rapor oluşturulamadı: {e}"


def generate_weekly_report(filtered_news: list[dict], upcoming_earnings: list[dict], markets: list[dict] | None = None, tr_news: list[dict] | None = None) -> str:
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
        + "\n\nPİYASA VERİLERİ (TradingView, kesin veri — endeks/fiyat rakamı verirken yalnızca bunları ve HAFTALIK değişimi kullan, rakam uydurma):\n"
        + _format_markets(markets)
        + "\n\nHAFTANIN HABERLERİ:\n" + news_text
        + "\n\nTÜRKİYE HABERLERİ (Bloomberg HT başlıkları — yalnızca 🇹🇷 bölümü için):\n" + _format_tr_news(tr_news)
        + "\n\nÖNÜMÜZDEKİ HAFTA EARNINGS:\n" + _format_upcoming_earnings(upcoming_earnings)
        + "\n\nKOD KURALLARI: POZİSYON FIRSATLARI'na yalnızca NYSE/Nasdaq'ta bugün işlem gören hisse/ETF kodlarını yaz; "
          "emin olmadığın, kapanmış, OTC, yabancı borsa, ters (short/bear) ya da kaldıraçlı (2x/3x) ETF kodlarını yazma."
        + f"\n\nYIL: İçinde bulunduğumuz yıl {datetime.now().year}; bu yılın olaylarını {datetime.now().year - 1} diye yazma."
    )

    try:
        return _create("claude-sonnet-5-5", content, "haftalık rapor")
    except Exception as e:
        log.error("Haftalık rapor hatası: %s", e)
        return f"Rapor oluşturulamadı: {e}"
