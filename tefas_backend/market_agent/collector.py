"""
Market Agent — Veri Toplayıcı
RSS feed'leri + Finnhub + NewsAPI + Alpha Vantage
"""
import os, requests, logging
from datetime import datetime, timedelta, date
from typing import Optional

log = logging.getLogger(__name__)

# ── Tarih ayrıştırma yardımcısı ───────────────────────────────────────────────
def _parse_pub_dt(date_str: str):
    """Çeşitli tarih formatlarını datetime'a çevirir. UTC timezone-aware döner."""
    from datetime import timezone
    if not date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        from datetime import datetime
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_str[:25], fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    except Exception:
        pass
    return None

# ── RSS Feed'leri ─────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "reuters_biz":      "https://news.google.com/rss/search?q=site:reuters.com+business+markets&hl=en-US&gl=US&ceid=US:en",
    "reuters_fin":      "https://news.google.com/rss/search?q=site:reuters.com+stocks+economy&hl=en-US&gl=US&ceid=US:en",
    "cnbc_markets":     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "marketwatch":      "http://feeds.marketwatch.com/marketwatch/topstories/",
    "wsj_business":     "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness",
    "wsj_markets":      "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "seeking_alpha":    "https://seekingalpha.com/market_currents.xml",
    "ft_markets":       "https://www.ft.com/markets?format=rss",
    # Bloomberg via Google News
    "bloomberg_mkts":   "https://news.google.com/rss/search?q=site:bloomberg.com+markets+stocks&hl=en-US&gl=US&ceid=US:en",
    "bloomberg_earn":   "https://news.google.com/rss/search?q=site:bloomberg.com+earnings&hl=en-US&gl=US&ceid=US:en",
    "bloomberg_theme":  "https://news.google.com/rss/search?q=site:bloomberg.com+thematic+sector+investing&hl=en-US&gl=US&ceid=US:en",
}


def fetch_rss_headlines(max_per_feed: int = 8) -> list[dict]:
    """Tüm RSS feed'lerinden başlık çek"""
    try:
        import feedparser
    except ImportError:
        log.error("feedparser kurulu değil: pip install feedparser")
        return []

    items = []
    for name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                items.append({
                    "source": name,
                    "title": title,
                    "summary": (entry.get("summary", "") or entry.get("description", ""))[:300].strip(),
                    "published": entry.get("published", ""),
                    "url": entry.get("link", ""),
                })
        except Exception as e:
            log.warning("RSS hata %s: %s", name, e)

    log.info("RSS: %d başlık çekildi", len(items))
    return items


def fetch_finnhub_news(api_key: str, category: str = "general") -> list[dict]:
    """Finnhub'dan finansal haber çek"""
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": category, "token": api_key},
            timeout=15,
        )
        items = []
        for item in r.json()[:20]:
            headline = item.get("headline", "").strip()
            if not headline:
                continue
            items.append({
                "source": f"finnhub_{category}",
                "title": headline,
                "summary": item.get("summary", "")[:300].strip(),
                "published": datetime.fromtimestamp(item.get("datetime", 0)).isoformat(),
                "url": item.get("url", ""),
            })
        log.info("Finnhub %s: %d haber", category, len(items))
        return items
    except Exception as e:
        log.warning("Finnhub hata %s: %s", category, e)
        return []


def fetch_newsapi(api_key: str, query: str, from_date: Optional[str] = None) -> list[dict]:
    """NewsAPI'den haber çek"""
    if not api_key:
        return []
    if not from_date:
        from_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "from": from_date,
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": 20,
                "apiKey": api_key,
            },
            timeout=15,
        )
        items = []
        for a in r.json().get("articles", []):
            title = a.get("title", "").strip()
            if not title or title == "[Removed]":
                continue
            items.append({
                "source": f"newsapi_{a.get('source', {}).get('name', 'unknown')}",
                "title": title,
                "summary": (a.get("description") or "")[:300].strip(),
                "published": a.get("publishedAt", ""),
                "url": a.get("url", ""),
            })
        log.info("NewsAPI '%s': %d haber", query[:30], len(items))
        return items
    except Exception as e:
        log.warning("NewsAPI hata: %s", e)
        return []


def fetch_earnings_today(finnhub_key: str) -> list[dict]:
    """Bugünkü earnings sonuçlarını çek"""
    if not finnhub_key:
        return []
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today, "to": tomorrow, "token": finnhub_key},
            timeout=15,
        )
        earnings = []
        for item in r.json().get("earningsCalendar", [])[:30]:
            earnings.append({
                "symbol":           item.get("symbol", ""),
                "date":             item.get("date", ""),
                "eps_estimate":     item.get("epsEstimate"),
                "eps_actual":       item.get("epsActual"),
                "rev_estimate":     item.get("revenueEstimate"),
                "rev_actual":       item.get("revenueActual"),
            })
        log.info("Bugünkü earnings: %d şirket", len(earnings))
        return earnings
    except Exception as e:
        log.warning("Earnings hata: %s", e)
        return []


def fetch_upcoming_earnings(finnhub_key: str, days: int = 7) -> list[dict]:
    """Önümüzdeki N günün earnings takvimine bak"""
    if not finnhub_key:
        return []
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": start, "to": end, "token": finnhub_key},
            timeout=15,
        )
        items = r.json().get("earningsCalendar", [])[:50]
        log.info("Gelecek %d günlük earnings: %d şirket", days, len(items))
        return items
    except Exception as e:
        log.warning("Upcoming earnings hata: %s", e)
        return []


# Bültenin PİYASALAR bölümü için kesin veri (model rakam uydurmasın diye)
MARKET_GROUPS = [
    ("ABD", [("SP:SPX", "S&P 500"), ("NASDAQ:NDX", "Nasdaq 100"), ("DJ:DJI", "Dow Jones"),
             ("TVC:RUT", "Russell 2000"), ("CBOE:VIX", "VIX")]),
    ("Avrupa", [("TVC:SX5E", "Euro Stoxx 50"), ("XETR:DAX", "DAX"), ("TVC:UKX", "FTSE 100"),
                ("EURONEXT:PX1", "CAC 40"), ("INDEX:FTSEMIB", "FTSE MIB"), ("BME:IBC", "IBEX 35")]),
    ("Asya", [("TVC:NI225", "Nikkei 225"), ("TVC:HSI", "Hang Seng"), ("SSE:000001", "Şanghay Bileşik"),
              ("KRX:KOSPI", "KOSPI"), ("NSE:NIFTY", "Nifty 50")]),
    ("Türkiye", [("BIST:XU100", "BIST 100"), ("FX_IDC:USDTRY", "USD/TRY")]),
    ("ABD vadeli (bu sabah)", [("CME_MINI:ES1!", "S&P 500 vadeli"), ("CME_MINI:NQ1!", "Nasdaq 100 vadeli")]),
    ("Döviz, faiz ve emtia", [("TVC:DXY", "Dolar Endeksi (DXY)"), ("FX_IDC:EURUSD", "EUR/USD"),
                              ("TVC:US10Y", "ABD 10Y faiz (%)"), ("TVC:GOLD", "Ons altın ($)"),
                              ("FX:UKOIL", "Brent petrol ($)"), ("BITSTAMP:BTCUSD", "Bitcoin ($)")]),
]


TR_NEWS_FEED = "https://www.bloomberght.com/rss"


def fetch_tr_news(hours: int = 28, limit: int = 40) -> list[dict]:
    """Bloomberg HT başlıkları (Türkiye bölümü için; global akıştan ayrı tutulur)."""
    try:
        import feedparser
        feed = feedparser.parse(TR_NEWS_FEED, agent="Mozilla/5.0")
    except Exception as e:
        log.warning("Bloomberg HT alınamadı: %s", e)
        return []
    from datetime import timezone as _tz
    cutoff = datetime.now(_tz.utc) - timedelta(hours=hours)
    out = []
    for e in feed.entries:
        dt = _parse_pub_dt(e.get("published", ""))
        if dt is not None and dt < cutoff:
            continue
        out.append({"title": e.get("title", "").strip(),
                    "summary": (e.get("summary", "") or "").strip()[:300],
                    "published": e.get("published", "")})
        if len(out) >= limit:
            break
    return out


def fetch_market_snapshot() -> list[dict]:
    """TradingView tarayıcısından son fiyat, günlük ve haftalık % değişim."""
    syms = [s for _, items in MARKET_GROUPS for s, _ in items]
    try:
        r = requests.post("https://scanner.tradingview.com/global/scan", timeout=15,
                          headers={"Origin": "https://www.tradingview.com", "User-Agent": "Mozilla/5.0"},
                          json={"symbols": {"tickers": syms, "query": {"types": []}},
                                "columns": ["close", "change", "Perf.W", "time"]})
        got = {x["s"]: x["d"] for x in r.json().get("data", [])}
    except Exception as e:
        log.warning("Piyasa verisi alınamadı: %s", e)
        return []
    out = []
    for group, items in MARKET_GROUPS:
        for sym, name in items:
            d = got.get(sym)
            if d and d[0] is not None:
                from zoneinfo import ZoneInfo
                day = datetime.fromtimestamp(d[3], ZoneInfo("Europe/Istanbul")).date() if len(d) > 3 and d[3] else None
                out.append({"group": group, "name": name, "price": d[0], "change": d[1], "week": d[2],
                            "date": day.isoformat() if day else None})   # verinin ait olduğu seans günü
    return out


def _daily_window_hours() -> int:
    """Günlük bülten haber penceresi.
    Hafta içi: son 28 saat. Pazartesi: Cumartesi 06:00'dan beri — Cuma'nın haberleri zaten
    Cumartesi bülteninde verildi, Pazartesi yalnızca hafta sonu çıkanları alır (~48 saat)."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('Europe/Istanbul'))
    if now.weekday() == 0:
        sat = (now - timedelta(days=2)).replace(hour=6, minute=0, second=0, microsecond=0)
        return max(int((now - sat).total_seconds() // 3600) + 1, 24)
    return 28


def collect_all(daily: bool = True) -> dict:
    """Tüm kaynaklardan veri topla"""
    finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
    newsapi_key = os.environ.get("NEWSAPI_KEY", "")

    lookback_days = (2 if _daily_window_hours() > 28 else 1) if daily else 7
    from_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    print("📡 RSS feed'leri çekiliyor...")
    rss = fetch_rss_headlines(max_per_feed=8)

    print("📊 Finnhub haberleri çekiliyor...")
    finnhub = fetch_finnhub_news(finnhub_key, "general")
    finnhub += fetch_finnhub_news(finnhub_key, "forex")
    finnhub += fetch_finnhub_news(finnhub_key, "merger")

    print("📰 NewsAPI çekiliyor...")
    newsapi  = fetch_newsapi(newsapi_key, "stock market earnings sector", from_date)
    newsapi += fetch_newsapi(newsapi_key, "thematic investing AI energy healthcare defense semiconductor", from_date)
    newsapi += fetch_newsapi(newsapi_key, "Fed interest rates inflation GDP", from_date)

    print("💰 Earnings çekiliyor...")
    week_earnings = []
    if daily:
        earnings = fetch_earnings_today(finnhub_key)
        if datetime.now().weekday() == 0:          # Pazartesi: haftanın bilanço takvimi
            week_earnings = fetch_upcoming_earnings(finnhub_key, days=5)
    else:
        earnings = fetch_upcoming_earnings(finnhub_key, days=7)

    all_news = rss + finnhub + newsapi

    # Deduplicate by title
    seen, unique = set(), []
    for item in all_news:
        key = item["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    # Tarih filtresi: günlük=28 saat, haftalık=8 gün
    from datetime import timezone as _tz
    cutoff_hours = _daily_window_hours() if daily else 8 * 24
    cutoff_dt = datetime.now(_tz.utc) - timedelta(hours=cutoff_hours)

    dated, undated = [], []
    for item in unique:
        dt = _parse_pub_dt(item.get("published", ""))
        if dt is None:
            undated.append(item)   # tarih yoksa dahil et
        elif dt >= cutoff_dt:
            dated.append(item)
        # else: eski haber, atla

    # Tarihli haberleri önce sırala (en yeni önce), ardından tarihi bilinmeyenleri ekle
    dated.sort(key=lambda x: _parse_pub_dt(x.get("published","")) or datetime.min.replace(tzinfo=_tz.utc), reverse=True)
    fresh = dated + undated[:max(0, 20 - len(dated))]  # en fazla 20 tarihi bilinmeyen

    print(f"✓ {len(unique)} benzersiz → {len(dated)} güncel ({cutoff_hours}h) + {len(undated[:max(0,20-len(dated))])} tarihi bilinmeyen, {len(earnings)} earnings")
    print("🇹🇷 Bloomberg HT çekiliyor...")
    tr_news = fetch_tr_news(hours=_daily_window_hours() if daily else 8 * 24)
    print(f"✓ {len(tr_news)} Türkiye haberi")

    print("📈 Piyasa verileri (TradingView) çekiliyor...")
    markets = fetch_market_snapshot()
    print(f"✓ {len(markets)} piyasa verisi")

    return {
        "news":         fresh,
        "earnings":     earnings,
        "markets":      markets,
        "tr_news":      tr_news,
        "week_earnings": week_earnings,
        "collected_at": datetime.now().isoformat(),
    }
