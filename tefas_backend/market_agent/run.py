"""
Market Agent — CLI Çalıştırıcı

Kullanım (PythonAnywhere bash):
    cd ~/tr-data-dashboard
    python tefas_backend/market_agent/run.py --daily
    python tefas_backend/market_agent/run.py --weekly
"""
import argparse, logging, os, sys
from pathlib import Path

# Proje kökünü path'e ekle
_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# .env yükle
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

from tefas_backend.market_agent.collector import collect_all
from tefas_backend.market_agent.analyzer  import filter_news, generate_daily_report, generate_weekly_report
from tefas_backend.market_agent.reports   import save_report


def run_daily() -> str:
    print("=" * 60)
    print("🗞️  GÜNLÜK RAPOR OLUŞTURULUYOR")
    print("=" * 60)

    data     = collect_all(daily=True)
    print("\n🤖 Haberler filtreleniyor (Claude Haiku)...")
    filtered = filter_news(data["news"])
    print(f"✓ {len(filtered)} haber seçildi\n")

    print("📝 Günlük rapor yazılıyor (Claude Sonnet)...")
    report = generate_daily_report(filtered, data["earnings"])

    save_report("daily", report)
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)
    return report


def run_weekly() -> str:
    print("=" * 60)
    print("📋 HAFTALIK RAPOR OLUŞTURULUYOR")
    print("=" * 60)

    data     = collect_all(daily=False)
    print("\n🤖 Haberler filtreleniyor (Claude Haiku)...")
    filtered = filter_news(data["news"])
    print(f"✓ {len(filtered)} haber seçildi\n")

    print("📝 Haftalık rapor yazılıyor (Claude Sonnet)...")
    report = generate_weekly_report(filtered, data["earnings"])

    save_report("weekly", report)
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Intelligence Agent")
    parser.add_argument("--daily",  action="store_true", help="Günlük rapor oluştur")
    parser.add_argument("--weekly", action="store_true", help="Haftalık rapor oluştur")
    args = parser.parse_args()

    if not args.daily and not args.weekly:
        print("Kullanım: python run.py --daily  veya  python run.py --weekly")
        sys.exit(1)

    if args.daily:
        run_daily()
    if args.weekly:
        run_weekly()
