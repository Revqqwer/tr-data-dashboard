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
    "FETH": "Fidelity",
    "ETHW": "Bitwise",
    "CETH": "21Shares",
    "ETHV": "VanEck",
    "QETH": "Invesco",
    "EZET": "Franklin",
    "ETH":  "Grayscale Mini",
    "ETHE": "Grayscale",
}

URLS = {
    "BTC": "https://farside.co.uk/btc/",
    "ETH": "https://farside.co.uk/eth/",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://farside.co.uk/",
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
    """'111.7' → 111.7,  '-' → 0.0,  '' → None"""
    s = s.strip()
    if s in ("", "-", "N/A", "n/a"):
        return 0.0
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
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Farside %s fetch hatası: %s", asset, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Sayfadaki ana veri tablosunu bul
    table = soup.find("table")
    if not table:
        log.error("Farside %s: tablo bulunamadı", asset)
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    # Header satırından sütun adlarını al
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True) for c in header_cells]

    # "Date" veya ilk sütun tarih kolonudur
    # Ticker adlarını normalize et (sütun başlıklarını ticker dict ile eşleştir)
    ticker_list = list(tickers.keys())

    records = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        date_str = cells[0].get_text(strip=True)
        trade_date = _parse_date(date_str)
        if not trade_date:
            continue

        # Her ticker için değer al
        for i, ticker in enumerate(ticker_list):
            col_idx = i + 1  # 0 = date
            if col_idx >= len(cells):
                break
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
        # ETH sayfasında üstte metadata satırları var (fee, seed vb.)
        # skiprows=4 ile gerçek veri satırlarından başla
        df_eth = pd.read_excel(filepath, sheet_name="eth flow", header=0, skiprows=4)
        # Sütun adları: Unnamed:0=Date, Blackrock, Fidelity, Bitwise, 21 Shares, VanEck, Invesco, Franklin, Grayscale, Grayscale.1, Total, ...
        eth_name_to_ticker = {
            "Blackrock":  "ETHA",
            "Fidelity":   "FETH",
            "Bitwise":    "ETHW",
            "21 Shares":  "CETH",
            "VanEck":     "ETHV",
            "Invesco":    "QETH",
            "Franklin":   "EZET",
            "Grayscale":  "ETH",
            "Grayscale.1": "ETHE",
        }
        records = []
        date_col = df_eth.columns[0]
        for _, row in df_eth.iterrows():
            date_raw = str(row[date_col]).strip()
            trade_date = _parse_date(date_raw)
            if not trade_date:
                continue
            for col_name, ticker in eth_name_to_ticker.items():
                if col_name not in df_eth.columns:
                    continue
                val = row[col_name]
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
