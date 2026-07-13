# -*- coding: utf-8 -*-
"""
Ozel Fonlar holdinglerindeki BIST hisselerinin GUNLUK % degisimini TradingView'dan
ceker ve data/bist_live.json'a yazar. PythonAnywhere'de SAATLIK scheduled task olarak
calisir; web endpoint bu dosyadan okur (web yolunda TradingView cagrisi olmaz → hizli+guvenli).

PA Scheduled Task (saatlik):
    cd ~/tr-data-dashboard && python fetch_bist_prices.py

Cikti (data/bist_live.json):
    {
      "updated_at": "2026-07-11T09:00:00+00:00",
      "prices": { "DSTKF": {"price": 3457.5, "prev_close": 3400.0, "change_pct": 1.69}, ... },
      "not_found": ["XXX", ...]
    }
"""
import os
import sys
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from tv_ws import _fetch_tv_ws

HOLDINGS_FILE = os.path.join(BASE, "data", "fund_holdings.json")
OUT_FILE = os.path.join(BASE, "data", "bist_live.json")


def _fetch_timeout(ticker, timeout=10.0):
    """WS fetch'i sinirli surede calistir — cozulemeyen sembol askida birakmasin."""
    import threading
    box = {}
    t = threading.Thread(target=lambda: box.update(r=_fetch_tv_ws(ticker, "BIST", n_bars=2)), daemon=True)
    t.start()
    t.join(timeout)
    return box.get("r")


def _unique_tickers() -> list:
    with open(HOLDINGS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    seen, out = set(), []
    for fund, holds in data.items():
        for h in holds:
            if h.get("type") == "byf":
                continue  # BYF/fon TradingView'da yok; endpoint TEFAS fund_daily'den bakar
            c = str(h.get("code", "")).strip().upper()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
    return out


def main():
    tickers = _unique_tickers()
    print(f"{len(tickers)} tekil ticker cekiliyor (TradingView BIST)...")
    prices = {}
    not_found = []
    for i, t in enumerate(tickers, 1):
        try:
            r = _fetch_timeout(t)
        except Exception as ex:
            r = {}
            print(f"  {t}: HATA {ex}")
        if not r or len(r) < 1:
            not_found.append(t)
            print(f"  [{i}/{len(tickers)}] {t}: fiyat yok")
            continue
        ds = sorted(r.keys())
        last = r[ds[-1]]
        prev = r[ds[-2]] if len(ds) >= 2 else None
        change = round((last / prev - 1.0) * 100, 2) if prev else None
        prices[t] = {"price": last, "prev_close": prev, "change_pct": change}
        print(f"  [{i}/{len(tickers)}] {t}: {last} ({'+' if (change or 0) >= 0 else ''}{change}%)")

    out = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "prices": prices,
        "not_found": not_found,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nYazildi: {OUT_FILE}")
    print(f"Fiyat bulunan: {len(prices)} | bulunamayan: {len(not_found)}")
    if not_found:
        print("Bulunamayanlar:", ", ".join(not_found))


if __name__ == "__main__":
    main()
