"""
Global Piyasa Takip — TradingView günlük bar collector + SQLite cache.

Kullanım (PythonAnywhere bash):
    cd ~/tr-data-dashboard
    python tefas_backend/global_market.py           # süresi dolanları güncelle
    python tefas_backend/global_market.py --force   # hepsini güncelle
"""
import argparse, json, logging, os, random, re, sqlite3, string, sys, time
from datetime import datetime, timedelta
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH    = os.path.join(_ROOT, "data", "global_market.db")
N_BARS     = 420   # ~14 aylık günlük bar (1Y görünümü için yeterli)
N_BARS_4H  = 210   # ~5 haftalık 4H bar (1W smooth grafik için)

# ── Ticker tanımları ──────────────────────────────────────────────────────────
TICKERS: dict[str, list[tuple[str, str]]] = {
    "faiz": [
        ("TVC:US10Y",  "US10Y"),
        ("TVC:US02Y",  "US02Y"),
        ("TVC:DE10Y",  "German10Y"),
        ("TVC:DE02Y",  "German2Y"),
        ("TVC:CH10Y",  "Swiss10Y"),
        ("TVC:GB10Y",  "UK10Y"),
    ],
    "fx": [
        ("TVC:DXY",        "DXY"),
        ("FX:EURUSD",      "EURUSD"),
        ("FX:GBPUSD",      "GBPUSD"),
        ("SAXO:JPYUSD",    "JPYUSD"),
        ("SAXO:CADUSD",    "CADUSD"),
        ("FX:EURGBP",      "EURGBP"),
        ("FX_IDC:BRLUSD",  "BRLUSD"),
        ("SAXO:CHFUSD",    "CHFUSD"),
        ("FX_IDC:CNHUSD",  "CNHUSD"),
        ("SAXO:MXNUSD",    "MXNUSD"),
        ("FX:AUDUSD",      "AUDUSD"),
        ("FX_IDC:ZARUSD",  "ZARUSD"),
    ],
    "commodity": [
        ("ICEUS:CC1!",        "Kakao"),
        ("FOREXCOM:COFFEE",   "Kahve"),
        ("CAPITALCOM:XCUUSD", "Bakır"),
        ("FOREXCOM:CORN",     "Mısır"),
        ("PEPPERSTONE:NATGAS","NATGAS"),
        ("CAPITALCOM:SOYBEAN","Soya"),
        ("BLACKBULL:BRENT",   "Brent"),
        ("FX:WHEATF",         "Buğday"),
        ("OANDA:XAGUSD",      "Gümüş"),
        ("OANDA:XAUUSD",      "Altın"),
        ("OANDA:XPTUSD",      "Platin"),
    ],
    "endeks": [
        ("CME_MINI:NQ1!", "Nasdaq"),
        ("CME_MINI:RTY1!","Russell"),
        ("CFI:EUR50",     "Eurostoxx"),
        ("XETR:DAX",      "DAX"),
        ("OSE:NK2251!",   "Nikkei"),
        ("NSE:NIFTY",     "Nifty"),
        ("FX:UK100",      "UK100"),
        ("FX:FRA40",      "CAC40"),
        ("HKEX:HSI1!",    "HSI"),
        ("TVC:KOSPI",     "KOSPI"),
        ("BIST:XU100",    "BIST100"),
    ],
}

ALL_TICKERS: dict[str, tuple[str, str]] = {
    tv_id: (label, cat)
    for cat, items in TICKERS.items()
    for tv_id, label in items
}


# ── TradingView WebSocket ─────────────────────────────────────────────────────

def _rand(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))

def _pack(func: str, args: list) -> str:
    body = json.dumps({"m": func, "p": args}, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"

def _parse(raw: str) -> list[str]:
    out = []
    while raw:
        m = re.match(r"^~m~(\d+)~m~", raw)
        if not m:
            break
        n = int(m.group(1)); start = m.end()
        out.append(raw[start: start + n])
        raw = raw[start + n:]
    return out


def fetch_tv_daily(tv_symbol: str, n_bars: int = N_BARS) -> list[tuple[str, float]]:
    """
    TradingView'dan günlük kapanış verisi çeker.
    Döner: [(date_str, close), ...]  örn. [('2025-01-02', 4.65), ...]
    """
    from websocket import create_connection, WebSocketTimeoutException

    url = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&date=&type=chart"
    ws  = create_connection(
        url,
        headers={"Origin": "https://www.tradingview.com"},
        timeout=20,
    )

    chart_sess = "cs_" + _rand()
    sym_json   = json.dumps({"adjustment": "splits", "symbol": tv_symbol})

    ws.send(_pack("set_auth_token",       ["unauthorized_user_token"]))
    ws.send(_pack("chart_create_session", [chart_sess, ""]))
    ws.send(_pack("resolve_symbol",       [chart_sess, "ser_1", f"={sym_json}"]))
    ws.send(_pack("create_series",        [chart_sess, "s1", "s1", "ser_1", "D", n_bars]))

    result: list[tuple[str, float]] = []
    for _ in range(80):
        try:
            raw = ws.recv()
        except WebSocketTimeoutException:
            continue
        except Exception:
            break

        for pkt in _parse(raw):
            if re.match(r"^~h~\d+$", pkt):
                ws.send(f"~m~{len(pkt)}~m~{pkt}")
                continue
            try:
                msg = json.loads(pkt)
            except Exception:
                continue

            if msg.get("m") == "timescale_update":
                bars = (
                    msg.get("p", [{}] * 2)[1]
                       .get("s1", {})
                       .get("s", [])
                )
                for bar in bars:
                    v = bar.get("v", [])
                    if len(v) >= 5:
                        ts    = v[0]
                        close = v[4]
                        date  = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                        result.append((date, round(float(close), 6)))
                if result:
                    ws.close()
                    return result

    ws.close()
    return result


def fetch_tv_4h(tv_symbol: str, n_bars: int = N_BARS_4H) -> list[tuple[str, float]]:
    """
    TradingView'dan 4 saatlik bar verisi çeker.
    Döner: [(datetime_str, close), ...]  örn. [('2025-01-02T08:00', 4.65), ...]
    """
    from websocket import create_connection, WebSocketTimeoutException

    url = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&date=&type=chart"
    ws  = create_connection(
        url,
        headers={"Origin": "https://www.tradingview.com"},
        timeout=20,
    )

    chart_sess = "cs_" + _rand()
    sym_json   = json.dumps({"adjustment": "splits", "symbol": tv_symbol})

    ws.send(_pack("set_auth_token",       ["unauthorized_user_token"]))
    ws.send(_pack("chart_create_session", [chart_sess, ""]))
    ws.send(_pack("resolve_symbol",       [chart_sess, "ser_1", f"={sym_json}"]))
    ws.send(_pack("create_series",        [chart_sess, "s1", "s1", "ser_1", "240", n_bars]))

    result: list[tuple[str, float]] = []
    for _ in range(80):
        try:
            raw = ws.recv()
        except WebSocketTimeoutException:
            continue
        except Exception:
            break

        for pkt in _parse(raw):
            if re.match(r"^~h~\d+$", pkt):
                ws.send(f"~m~{len(pkt)}~m~{pkt}")
                continue
            try:
                msg = json.loads(pkt)
            except Exception:
                continue

            if msg.get("m") == "timescale_update":
                bars = (
                    msg.get("p", [{}] * 2)[1]
                       .get("s1", {})
                       .get("s", [])
                )
                for bar in bars:
                    v = bar.get("v", [])
                    if len(v) >= 5:
                        ts    = v[0]
                        close = v[4]
                        dt    = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M")
                        result.append((dt, round(float(close), 6)))
                if result:
                    ws.close()
                    return result

    ws.close()
    return result


# ── SQLite ────────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker TEXT NOT NULL,
                date   TEXT NOT NULL,
                close  REAL,
                PRIMARY KEY (ticker, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices_4h (
                ticker TEXT NOT NULL,
                dt     TEXT NOT NULL,
                close  REAL,
                PRIMARY KEY (ticker, dt)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                ticker          TEXT PRIMARY KEY,
                last_fetched    TEXT,
                last_fetched_4h TEXT
            )
        """)
        # Mevcut DB'ye last_fetched_4h sütunu ekle (migration)
        try:
            conn.execute("ALTER TABLE meta ADD COLUMN last_fetched_4h TEXT")
        except Exception:
            pass
        conn.commit()


def _needs_refresh(conn: sqlite3.Connection, tv_id: str, max_age_h: int = 23) -> bool:
    row = conn.execute(
        "SELECT last_fetched FROM meta WHERE ticker=?", (tv_id,)
    ).fetchone()
    if not row or not row[0]:
        return True
    last = datetime.fromisoformat(row[0])
    return (datetime.utcnow() - last).total_seconds() > max_age_h * 3600


def _upsert(conn: sqlite3.Connection, tv_id: str, rows: list[tuple[str, float]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO prices(ticker, date, close) VALUES(?,?,?)",
        [(tv_id, d, c) for d, c in rows],
    )
    conn.execute("""
        INSERT INTO meta(ticker, last_fetched) VALUES(?,?)
        ON CONFLICT(ticker) DO UPDATE SET last_fetched=excluded.last_fetched
    """, (tv_id, datetime.utcnow().isoformat()))
    conn.commit()


def _needs_refresh_4h(conn: sqlite3.Connection, tv_id: str, max_age_h: int = 23) -> bool:
    row = conn.execute(
        "SELECT last_fetched_4h FROM meta WHERE ticker=?", (tv_id,)
    ).fetchone()
    if not row or not row[0]:
        return True
    last = datetime.fromisoformat(row[0])
    return (datetime.utcnow() - last).total_seconds() > max_age_h * 3600


def _upsert_4h(conn: sqlite3.Connection, tv_id: str, rows: list[tuple[str, float]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO prices_4h(ticker, dt, close) VALUES(?,?,?)",
        [(tv_id, d, c) for d, c in rows],
    )
    conn.execute("""
        INSERT INTO meta(ticker, last_fetched_4h) VALUES(?,?)
        ON CONFLICT(ticker) DO UPDATE SET last_fetched_4h=excluded.last_fetched_4h
    """, (tv_id, datetime.utcnow().isoformat()))
    conn.commit()


# ── Toplama ───────────────────────────────────────────────────────────────────

def collect_all(force: bool = False) -> dict[str, object]:
    """Tüm ticker'ları sırayla çek ve cache'e yaz."""
    try:
        import websocket  # noqa
    except ImportError:
        log.error("websocket-client kurulu değil: pip install websocket-client")
        return {}

    init_db()
    results: dict[str, object] = {}

    with _get_conn() as conn:
        for tv_id, (label, cat) in ALL_TICKERS.items():
            if not force and not _needs_refresh(conn, tv_id):
                log.debug("Atlanıyor (güncel): %s", tv_id)
                results[tv_id] = "skip"
                continue

            log.info("Çekiliyor: %s (%s)…", tv_id, label)
            try:
                rows = fetch_tv_daily(tv_id)
                if rows:
                    _upsert(conn, tv_id, rows)
                    results[tv_id] = len(rows)
                    log.info("  → %d bar kaydedildi", len(rows))
                else:
                    results[tv_id] = 0
                    log.warning("  → Veri gelmedi")
            except Exception as exc:
                results[tv_id] = str(exc)
                log.error("  → Hata: %s", exc)
            time.sleep(1.5)

    return results


def collect_all_4h(force: bool = False) -> dict[str, object]:
    """Tüm ticker'ların 4H barlarını çek ve cache'e yaz."""
    try:
        import websocket  # noqa
    except ImportError:
        log.error("websocket-client kurulu değil: pip install websocket-client")
        return {}

    init_db()
    results: dict[str, object] = {}

    with _get_conn() as conn:
        for tv_id, (label, cat) in ALL_TICKERS.items():
            if not force and not _needs_refresh_4h(conn, tv_id):
                log.debug("4H atlanıyor (güncel): %s", tv_id)
                results[tv_id] = "skip"
                continue

            log.info("4H çekiliyor: %s (%s)…", tv_id, label)
            try:
                rows = fetch_tv_4h(tv_id)
                if rows:
                    _upsert_4h(conn, tv_id, rows)
                    results[tv_id] = len(rows)
                    log.info("  → %d bar kaydedildi", len(rows))
                else:
                    results[tv_id] = 0
                    log.warning("  → Veri gelmedi")
            except Exception as exc:
                results[tv_id] = str(exc)
                log.error("  → Hata: %s", exc)
            time.sleep(1.5)

    return results


# ── API için veri okuma ───────────────────────────────────────────────────────

def get_all_data() -> dict[str, dict]:
    """
    API endpoint'ine dönecek veriyi hazırla.
    {
      "TVC:US10Y": {
        "label": "US10Y",
        "category": "faiz",
        "data": [{"date": "2025-01-02", "close": 4.65}, ...]
      },
      ...
    }
    """
    cutoff = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d")
    init_db()
    out: dict[str, dict] = {}
    with _get_conn() as conn:
        for tv_id, (label, cat) in ALL_TICKERS.items():
            rows = conn.execute(
                "SELECT date, close FROM prices WHERE ticker=? AND date>=? ORDER BY date",
                (tv_id, cutoff),
            ).fetchall()
            if rows:
                out[tv_id] = {
                    "label":    label,
                    "category": cat,
                    "data":     [{"date": d, "close": c} for d, c in rows],
                }
    return out


def get_4h_data() -> dict[str, dict]:
    """
    4H veri — son 35 günlük 4H barlar.
    {
      "TVC:US10Y": {
        "label": "US10Y",
        "category": "faiz",
        "data": [{"dt": "2025-01-02T08:00", "close": 4.65}, ...]
      }, ...
    }
    """
    cutoff = (datetime.utcnow() - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M")
    init_db()
    out: dict[str, dict] = {}
    with _get_conn() as conn:
        for tv_id, (label, cat) in ALL_TICKERS.items():
            rows = conn.execute(
                "SELECT dt, close FROM prices_4h WHERE ticker=? AND dt>=? ORDER BY dt",
                (tv_id, cutoff),
            ).fetchall()
            if rows:
                out[tv_id] = {
                    "label":    label,
                    "category": cat,
                    "data":     [{"dt": d, "close": c} for d, c in rows],
                }
    return out


def get_last_updated() -> Optional[str]:
    """Cache'in en son güncellendiği zamanı döner."""
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(last_fetched) FROM meta"
        ).fetchone()
    return row[0] if row and row[0] else None


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Global piyasa verisi güncelle")
    parser.add_argument("--force",       action="store_true", help="Cache süresi dolmamış olanları da güncelle")
    parser.add_argument("--daily-only",  action="store_true", help="Sadece günlük (1D) veri")
    parser.add_argument("--4h-only",     dest="four_h_only", action="store_true", help="Sadece 4H veri")
    args = parser.parse_args()

    def _print_stats(label: str, res: dict) -> None:
        ok   = sum(1 for v in res.values() if isinstance(v, int) and v > 0)
        skip = sum(1 for v in res.values() if v == "skip")
        fail = sum(1 for v in res.values() if isinstance(v, str) and v != "skip")
        print(f"\n── {label} ──")
        print(f"Güncellendi : {ok}")
        print(f"Atlandı     : {skip}  (güncel)")
        print(f"Hata        : {fail}")
        print(f"Toplam      : {len(res)}")

    if not args.four_h_only:
        _print_stats("1D Günlük Sonuç", collect_all(force=args.force))

    if not args.daily_only:
        _print_stats("4H Sonuç", collect_all_4h(force=args.force))
