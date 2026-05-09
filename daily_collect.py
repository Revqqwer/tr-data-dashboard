"""
Günlük otomatik veri güncelleme — PythonAnywhere Scheduled Task için.

Son 3 günü çeker; geç gelen / eksik kalan veriler tamamlanır.
Log: ~/tr-data-dashboard/logs/collect_YYYY-MM-DD.log
"""

import datetime
import logging
import os
import sys

# Proje kökünü path'e ekle
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Log dizini
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

today = datetime.date.today()
log_file = os.path.join(LOG_DIR, f"collect_{today}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

log.info("=== Günlük güncelleme başlıyor: %s ===", today)

try:
    from tefas_backend.collector import collect_range

    end   = today
    start = end - datetime.timedelta(days=3)

    log.info("Çekiliyor: %s -> %s", start, end)
    collect_range(start, end, skip_composition=False, batch_days=7)
    log.info("=== Tamamlandı ===")

except Exception as e:
    log.exception("Hata: %s", e)
    sys.exit(1)
