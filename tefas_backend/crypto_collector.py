"""
Kripto ETF para akışı toplayıcı.

Kaynaklar:
  BTC: https://farside.co.uk/btc/
  ETH: https://farside.co.uk/eth/

Kullanım:
  from tefas_backend.crypto_collector import collect_all, import_from_excel
  collect_all()                          # farside'dan çek
  import_from_excel("btc&ethflow.xlsx")  # Excel'den yükle
"""

import datetime
import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from .database import CryptoEtfFlow, engine

log = logging.getLogger(__name__)

# ── Metadata ────────────────────────────────────────────────────────────────

BTC_TICKERS: dict[str, str] = {
    "IBIT": "BlackRock",
    "FBTC": "Fidelity",
    "BITB": "Bitwise",
    "ARKB": "ARK/21Shares",
    "BTCO": "Invesco",
    "EZBC": "Franklin",
    "BRRR": "Valkyrie",
    "HODL": "VanEck",
    "BTCW": "WisdomTree",
    "MSBT": "Hashdex",
    "GBTC": "Grayscale",
    "BTC":  "Grayscale Mini",
}

ETH_TICKERS: dict[str, str] = {
    "ETHA": "BlackRock",
    "ETHB": "BlackRock",
    "FETH": "Fidelity",
    "ETHW": "Bitwise",
    "TETH": "21Shares",
    "CETH": "21Shares",   # tarihi veri için
    "ETHV": "VanEck",
    "QETH": "Invesco",
    "EZET": "Franklin",
    "ETH":  "Grayscale Mini",
    "ETHE": "Grayscale",
}

URLS = {
    "BTC": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "ETH": "https://farside.co.uk/ethereum-etf-flow-all-data/",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://farside.co.uk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "DNT": "1",
}

# ── Tarih parse ──────────────────────────────────────────────────────────────

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_date(s: str) -> Optional[datetime.date]:
    """'11 Jan 2024' → date(2024, 1, 11)"""
    s = s.strip()
    parts = s.split()
    if len(parts) != 3:
        return None
    try:
        day  = int(parts[0])
        mon  = MONTHS.get(parts[1])
        year = int(parts[2])
        if mon is None:
            return None
        return datetime.date(year, mon, day)
    except (ValueError, TypeError):
        return None


def _parse_flow(s: str) -> Optional[float]:
    """'111.7' → 111.7,  '(32.9)' → -32.9,  '-' / '—' → 0.0"""
    s = s.strip()
    if s in ("", "-", "—", "N/A", "n/a"):
        return 0.0
    if s.startswith("(") and s.endswith(")"):
        try:
            return -float(s[1:-1].replace(",", ""))
        except ValueError:
            return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


# ── Farside scraper ──────────────────────────────────────────────────────────

def _scrape_farside(asset: str) -> list[dict]:
    """farside.co.uk'tan ETF akış tablosunu çeker."""
    url = URLS[asset]
    tickers = BTC_TICKERS if asset == "BTC" else ETH_TICKERS

    try:
        try:
            import cloudscraper
            session = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        except ImportError:
            session = requests.Session()

        resp = session.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        log.error("Farside %s fetch hatası: %s", asset, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # En fazla satırı olan tabloyu bul (nav/footer tablolarını atla)
    all_tables = soup.find_all("table")
    table = max(all_tables, key=lambda t: len(t.find_all("tr")), default=None)
    if not table:
        log.error("Farside %s: tablo bulunamadı", asset)
        return []

    rows = table.find_all("tr")
    if len(rows) < 3:
        return []

    # Row 0: genel başlık (Total vb.), Row 1: ticker adları, Row 2+: veri
    ticker_row = rows[1].find_all(["td", "th"])
    ticker_list = list(tickers.keys())

    # Ticker adlarını sütun pozisyonuna göre eşleştir
    col_to_ticker: dict[int, str] = {}
    for i, cell in enumerate(ticker_row):
        name = cell.get_text(strip=True)
        if name in tickers:
            col_to_ticker[i] = name

    # Eğer row 1'den eşleşme yoksa (sayfa yapısı farklı), sabit sırayı kullan
    if not col_to_ticker:
        for i, ticker in enumerate(ticker_list):
            col_to_ticker[i + 1] = ticker

    records = []
    for row in rows[2:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        date_str = cells[0].get_text(strip=True)
        trade_date = _parse_date(date_str)
        if not trade_date:
            continue

        for col_idx, ticker in col_to_ticker.items():
            if col_idx >= len(cells):
                continue
            val_str = cells[col_idx].get_text(strip=True)
            flow = _parse_flow(val_str)
            records.append({
                "trade_date": trade_date,
                "asset":      asset,
                "ticker":     ticker,
                "fund_name":  tickers[ticker],
                "flow_usd_m": flow,
            })

    log.info("Farside %s: %d kayıt parse edildi", asset, len(records))
    return records


# ── DB upsert ────────────────────────────────────────────────────────────────

def _upsert(session: Session, records: list[dict]) -> int:
    """Kayıtları DB'ye INSERT OR REPLACE yap."""
    count = 0
    for r in records:
        existing = session.exec(
            select(CryptoEtfFlow).where(
                CryptoEtfFlow.trade_date == r["trade_date"],
                CryptoEtfFlow.asset      == r["asset"],
                CryptoEtfFlow.ticker     == r["ticker"],
            )
        ).first()

        if existing:
            existing.flow_usd_m = r["flow_usd_m"]
            existing.fund_name  = r["fund_name"]
            session.add(existing)
        else:
            session.add(CryptoEtfFlow(**r))
        count += 1

    session.commit()
    return count


# ── Ana toplama fonksiyonları ────────────────────────────────────────────────

def collect_asset(asset: str) -> int:
    """Tek bir asset (BTC veya ETH) için farside'dan veri çeker."""
    records = _scrape_farside(asset)
    if not records:
        return 0
    # Bugün dahil tüm veriyi kaydet; upsert sayesinde eksik/geç gelen
    # veriler bir sonraki çalıştırmada üzerine yazılır
    with Session(engine) as session:
        n = _upsert(session, records)
    log.info("%s: %d kayıt DB'ye yazıldı", asset, n)
    return n


def collect_all() -> dict[str, int]:
    """BTC ve ETH için farside'dan veri çeker."""
    results = {}
    for asset in ("BTC", "ETH"):
        results[asset] = collect_asset(asset)
        time.sleep(2)  #礼貌
    return results


# ── Excel import ─────────────────────────────────────────────────────────────

def import_from_excel(filepath: str) -> dict[str, int]:
    """
    Mevcut Excel dosyasından (btc&ethflow.xlsx) tarihi veriyi DB'ye yükler.
    Sadece bir kez çalıştırılması gerekir.
    """
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas yüklü değil: pip install pandas openpyxl")
        return {}

    counts = {"BTC": 0, "ETH": 0}

    # ── BTC sheet ──────────────────────────────────────────────────────
    try:
        df_btc = pd.read_excel(filepath, sheet_name="btc flow", header=0)
        # İlk sütun Date, sonrakiler IBIT FBTC ... Total
        btc_cols = list(BTC_TICKERS.keys())
        records = []
        for _, row in df_btc.iterrows():
            date_raw = str(row.iloc[0]).strip()
            trade_date = _parse_date(date_raw)
            if not trade_date:
                continue
            for ticker in btc_cols:
                if ticker not in df_btc.columns:
                    continue
                val = row[ticker]
                if pd.isna(val):
                    flow = 0.0
                elif isinstance(val, str):
                    flow = _parse_flow(val)
                else:
                    flow = float(val)
                records.append({
                    "trade_date": trade_date,
                    "asset":      "BTC",
                    "ticker":     ticker,
                    "fund_name":  BTC_TICKERS[ticker],
                    "flow_usd_m": flow if flow is not None else 0.0,
                })
        with Session(engine) as session:
            counts["BTC"] = _upsert(session, records)
        log.info("Excel BTC: %d kayıt yüklendi", counts["BTC"])
    except Exception as e:
        log.error("Excel BTC yükleme hatası: %s", e)

    # ── ETH sheet ──────────────────────────────────────────────────────
    try:
        # Row 0: fund names (Blackrock, Fidelity, ...)
        # Rows 1-5: metadata (tickers, fee, seed, empty)
        # Row 6+: actual data ("23 Jul 2024", ...)
        header_row = pd.read_excel(filepath, sheet_name="eth flow", header=None, nrows=1)
        fund_names = header_row.iloc[0].tolist()  # [nan, 'Blackrock', 'Fidelity', ...]

        # Map fund name → our canonical ticker (by column position)
        fund_to_ticker = {
            "Blackrock": "ETHA",
            "Fidelity":  "FETH",
            "Bitwise":   "ETHW",
            "21 Shares": "CETH",
            "VanEck":    "ETHV",
            "Invesco":   "QETH",
            "Franklin":  "EZET",
        }
        # Build col_index → ticker mapping
        # Two "Grayscale" cols: first = ETH (mini), second = ETHE
        grayscale_count = 0
        col_ticker: dict[int, str] = {}
        for i, name in enumerate(fund_names):
            if not isinstance(name, str):
                continue
            name = name.strip()
            if name in fund_to_ticker:
                col_ticker[i] = fund_to_ticker[name]
            elif name == "Grayscale":
                grayscale_count += 1
                col_ticker[i] = "ETH" if grayscale_count == 1 else "ETHE"

        # Read data rows (skip first 6 rows)
        df_eth = pd.read_excel(filepath, sheet_name="eth flow", header=None, skiprows=6)
        records = []
        for _, row in df_eth.iterrows():
            date_raw = str(row.iloc[0]).strip()
            trade_date = _parse_date(date_raw)
            if not trade_date:
                continue
            for col_idx, ticker in col_ticker.items():
                if col_idx >= len(row):
                    continue
                val = row.iloc[col_idx]
                if pd.isna(val):
                    flow = 0.0
                elif isinstance(val, str):
                    flow = _parse_flow(val)
                else:
                    flow = float(val)
                records.append({
                    "trade_date": trade_date,
                    "asset":      "ETH",
                    "ticker":     ticker,
                    "fund_name":  ETH_TICKERS.get(ticker, ticker),
                    "flow_usd_m": flow if flow is not None else 0.0,
                })
        with Session(engine) as session:
            counts["ETH"] = _upsert(session, records)
        log.info("Excel ETH: %d kayıt yüklendi", counts["ETH"])
    except Exception as e:
        log.error("Excel ETH yükleme hatası: %s", e)

    return counts
