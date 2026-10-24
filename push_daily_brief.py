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


TG_MARK = MARK.with_name('.daily_brief_telegram_sent')


def _telegram(today: str, text: str):
    """"3N Finans - Market Review" Telegram kanalına kısa duyuru (günde bir kez).
    Bot: @n3finans_yayin_bot. env: MARKET_REVIEW_TELEGRAM_BOT_TOKEN, MARKET_REVIEW_TELEGRAM_CHAT_ID.
    (MARKET_TELEGRAM_* başka bot/grup — 3N Crypto — burada KULLANILMAZ.)
    parse_mode gönderilmez: metindeki * / _ tüm mesajı reddettirir."""
    import os
    import requests
    token, chat = os.environ.get('MARKET_REVIEW_TELEGRAM_BOT_TOKEN', ''), os.environ.get('MARKET_REVIEW_TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        print('telegram: MARKET_REVIEW_TELEGRAM_BOT_TOKEN / MARKET_REVIEW_TELEGRAM_CHAT_ID eksik, atlandı')
        return
    if TG_MARK.exists() and TG_MARK.read_text().strip() == today:
        print('telegram: bugün zaten gönderildi')
        return
    try:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          data={'chat_id': chat, 'text': '☀️ ' + text}, timeout=20)
        print('telegram:', 'ok' if r.ok else r.text[:200])
        if r.ok:
            TG_MARK.parent.mkdir(parents=True, exist_ok=True)
            TG_MARK.write_text(today)
    except Exception as e:
        print('telegram hatası:', e)


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
    link = 'https://www.3nfinans.com/dashboard#market-briefs'   # kullanıcı tercihi (2026-10-24): panel linki
    if DRY:
        print('[dry] telegram: ☀️ Günaydın, ' + body[0].lower() + body[1:] + ' | ' + link)
        return

    _telegram(today, 'Günaydın, ' + body[0].lower() + body[1:] + '\n' + link)

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
