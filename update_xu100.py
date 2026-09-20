# -*- coding: utf-8 -*-
"""
BİST 100 (XU100) günlük kapanışlarını TradingView'dan çekip data/xu100_daily.json'a birleştirir.
BOFA Takip sayfasının korelasyon/trend analizleri bu dosyayı kullanır.

PythonAnywhere scheduled task (günlük, BIST kapanışından sonra — örn. 17:00 UTC):
    cd ~/tr-data-dashboard && python3 update_xu100.py
"""
import json
import os
import sys
from datetime import datetime

import collect_bist

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'xu100_daily.json')
N_BARS = 1200          # ~4,7 yıl; eksik günleri de kapatır


def main() -> int:
    dates, closes = collect_bist.fetch_tv_ws('XU100', 'BIST', N_BARS, '1D')
    if not dates:
        print('HATA: TradingView verisi alınamadı, dosya değiştirilmedi.')
        return 1

    merged = {}
    try:
        with open(PATH, encoding='utf-8') as f:
            old = json.load(f)
        merged.update(zip(old['dates'], old['closes']))
    except Exception:
        pass
    before = len(merged)
    merged.update(zip(dates, closes))          # TradingView değeri eskiyi ezer

    ds = sorted(merged)
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'dates': ds, 'closes': [merged[d] for d in ds]}, f)
    os.replace(tmp, PATH)                       # atomik: yarım dosya okunmaz
    print(f'{datetime.now():%Y-%m-%d %H:%M} XU100: {before} -> {len(ds)} gün, son {ds[-1]} = {merged[ds[-1]]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
