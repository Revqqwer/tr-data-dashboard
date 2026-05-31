"""
Market Agent — Veri Toplayıcı
RSS feed'leri + Finnhub + NewsAPI + Alpha Vantage
"""
import os, requests, logging
from datetime import datetime, timedelta, date
from typing import Optional

log = logging.getLogger(__name__)

# ── RSS Feed'leri ─────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "reuters_biz":      "https://feeds.reuters.com/reuters/businessNews",
    "reuters_fin":      "https://feeds.reuters.com/reuters/financialNewsOnly",
    "cnbc_markets":     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "marketwatch":      "http://feeds.marketwatch.com/marketwatch/topstories/",
    "wsj_business":     "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "seeking_alpha":    "https://seekingalpha.com/market_currents.xml",
    "yahoo_finance":    "https://finance.yahoo.com/news/rssindex",
    "ft_markets":       "https://www.ft.com/rss/home/us",
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


def collect_all(daily: bool = True) -> dict:
    """Tüm kaynaklardan veri topla"""
    finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
    newsapi_key = os.environ.get("NEWSAPI_KEY", "")

    lookback_days = 1 if daily else 7
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
    if daily:
        earnings = fetch_earnings_today(finnhub_key)
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

    print(f"✓ Toplam {len(unique)} benzersiz haber, {len(earnings)} earnings")
    return {
        "news":         unique,
        "earnings":     earnings,
        "collected_at": datetime.now().isoformat(),
    }
