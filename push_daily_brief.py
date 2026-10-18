# -*- coding: utf-8 -*-
"""
Günlük piyasa bülteni bildirimi — her gün 09:30'da (İstanbul).

Bülten 06:00'da hazırlanır (market_agent run_daily); bildirim kullanıcıyı
sabah erken uyandırmasın diye bu betikle 09:30'da gider.

PA zamanlanmış görev (PA saati UTC):  09:30 İstanbul = 06:30 UTC
    cd ~/tr-data-dashboard && python3 push_daily_brief.py
Saatlik de çalıştırılabilir: 09:30'dan önce hiçbir şey göndermez, gün içinde bir kez gönderir.
Deneme: python3 push_daily_brief.py --dry
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

DRY = '--dry' in sys.argv
FORCE = '--force' in sys.argv
TZ = ZoneInfo('Europe/Istanbul')
SEND_AFTER = (9, 30)
MARK = Path(__file__).resolve().parent / 'data' / '.daily_brief_push_sent'


def main():
    now = datetime.now(TZ)
    today = now.strftime('%Y-%m-%d')

    if not FORCE and (now.hour, now.minute) < SEND_AFTER:
        print(f'{now:%H:%M} — henüz 09:30 değil, gönderilmedi')
        return
    if not FORCE and MARK.exists() and MARK.read_text().strip() == today:
        print('bugün zaten gönderildi')
        return

    from tefas_backend.market_agent.reports import get_latest
    # Pazar günleri günlük yerine haftalık bülten hazırlanır → bildirim de ona göre
    rep, body = get_latest('daily'), 'Sabah bülteni çıktı!'
    if not rep or rep.get('date') != today:
        rep, body = get_latest('weekly'), 'Haftalık bülten çıktı!'
    if not rep or rep.get('date') != today:
        print('bugünün bülteni yok, gönderilmedi')
        return

    if DRY:
        print('[dry] gönderilecek: Kahveni kap gel!', body, '—', rep.get('title'))
        return

    import push
    res = push.send_push(
        '☕ Kahveni kap gel!',
        body,
        url='/dashboard#market-briefs', tag='daily-brief',
        route='briefs:' + rep.get('type', 'daily'),   # uygulama: Bülten sekmesi, doğru alt sekme
    )
    print('push:', res)
    if res.get('ok'):
        MARK.parent.mkdir(parents=True, exist_ok=True)
        MARK.write_text(today)


if __name__ == '__main__':
    main()
