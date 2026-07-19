# -*- coding: utf-8 -*-
"""
Tematik Fırsat Getirisi — ön hesaplama (PythonAnywhere'de çalıştırılır)

Her market-brief raporunun TEMATİK FIRSATLAR temalarındaki TÜM long ticker/ETF'lerini
çıkarır, raporun çıktığı günün AÇILIŞ fiyatından bugüne getiriyi TradingView'dan hesaplar
ve sonucu data/brief_positions.json'a yazar. Web tarafı bu dosyayı okur (canlı WS çağrısı
yapmaz → sayfa hızlı kalır).

Fiyatlar data/brief_prices_cache.json'da cache'lenir; --stale-hours'tan eski olanlar
yeniden çekilir (güncel fiyatı tazelemek için). Giriş fiyatları zaten sabittir.

Kullanım (PA Bash):
    cd ~/tr-data-dashboard && python compute_brief_positions.py
Zamanlanmış görev olarak günde bir çalıştırılabilir (fiyatları güncel tutar).
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from tefas_backend.market_agent.reports import get_reports          # noqa: E402
from tefas_backend.market_agent.positions import compute_positions, all_tickers  # noqa: E402
from tv_ws import fetch_ohlc                                        # noqa: E402

CACHE_PATH = _ROOT / "data" / "brief_prices_cache.json"
OUT_PATH = _ROOT / "data" / "brief_positions.json"
N_BARS = 260


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _now():
    return datetime.now(timezone.utc)


def main(stale_hours: float = 6.0):
    all_reports = get_reports(limit=1000)
    # Haftalık raporlar günlüklerin özeti — pozisyon tablosu üretme
    reports = [r for r in all_reports if r.get("type") != "weekly"]
    print(f"{len(all_reports)} rapor ({len(reports)} günlük, haftalıklar atlandı).")

    # Tüm raporlardaki benzersiz ticker'lar
    tickers = []
    for r in reports:
        for tk in all_tickers(r.get("content", "")):
            if tk not in tickers:
                tickers.append(tk)
    print(f"{len(tickers)} benzersiz ticker.")

    cache = _load_json(CACHE_PATH, {})
    cutoff = _now().timestamp() - stale_hours * 3600
    fetched = skipped = failed = 0

    for i, tk in enumerate(tickers, 1):
        entry = cache.get(tk)
        fresh = False
        if entry and entry.get("fetched"):
            try:
                fresh = datetime.fromisoformat(entry["fetched"]).timestamp() >= cutoff
            except Exception:
                fresh = False
        if fresh:
            skipped += 1
            continue
        try:
            bars = fetch_ohlc(tk, n_bars=N_BARS)
        except Exception as e:
            bars = {}
            print(f"  [{i}/{len(tickers)}] {tk} HATA: {e}")
        cache[tk] = {"fetched": _now().isoformat(), "bars": bars}
        if bars:
            fetched += 1
        else:
            failed += 1
        if i % 20 == 0:
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  ...{i}/{len(tickers)} (çekildi:{fetched} atlandı:{skipped} boş:{failed})")
        time.sleep(0.15)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"Fiyatlar: çekildi {fetched}, cache {skipped}, çözülemedi {failed}.")

    def lookup(tk):
        e = cache.get(tk)
        if e and e.get("bars"):
            return {"bars": e["bars"]}
        return None

    out = {}
    computed_at = _now().isoformat()
    for r in reports:
        rid = r.get("id")
        if not rid:
            continue
        res = compute_positions(r, lookup)
        res["computed_at"] = computed_at
        out[rid] = res

    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    total = sum(1 for v in out.values() if v.get("count"))
    print(f"✓ {OUT_PATH.name} yazıldı — {total}/{len(out)} raporda pozisyon var.")


if __name__ == "__main__":
    hrs = 6.0
    for a in sys.argv[1:]:
        if a.startswith("--stale-hours="):
            hrs = float(a.split("=", 1)[1])
    main(stale_hours=hrs)
