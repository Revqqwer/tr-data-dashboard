"""
Kripto ETF akış verilerini farside.co.uk'tan çeker.
PythonAnywhere'de günlük çalıştırılır.

Komut:
  cd /home/hakandeveli24/tr-data-dashboard && python daily_crypto_collect.py
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from tefas_backend.database import init_db
from tefas_backend.crypto_collector import collect_all

if __name__ == "__main__":
    init_db()
    results = collect_all()
    for asset, count in results.items():
        logging.info("%s: %d kayıt güncellendi", asset, count)
    logging.info("Kripto ETF veri toplama tamamlandı.")
