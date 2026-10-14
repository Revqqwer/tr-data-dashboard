# -*- coding: utf-8 -*-
"""
BOFA verisi geride kaldıysa admin'e e-posta hatırlatması.

Veri Fintables'tan tarayıcı script'iyle (Tampermonkey, bofa_sync.user.js) gelir; script ancak
Fintables açılınca çalıştığı için veri birikebilir. Bu görev her akşam bakar:
  • Sitedeki son BOFA günü ile beklenen son iş günü arasında ≥ REMIND_LAG iş günü fark varsa
  • ve son hatırlatmadan beri ≥ REMIND_EVERY gün geçtiyse
admin hesabının (ADMIN_USERS) e-posta adresine kısa bir hatırlatma gönderir.

PA zamanlanmış görev (UTC):  16:30 (= 19:30 İstanbul)
    cd ~/tr-data-dashboard && python3.10 bofa_reminder.py
Deneme: python3.10 bofa_reminder.py --dry
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

DRY = '--dry' in sys.argv
REMIND_LAG = 3          # bu kadar iş günü geride kalınca hatırlat
REMIND_EVERY = 3        # aynı hatırlatmayı en sık bu kadar günde bir gönder
ROOT = Path(__file__).resolve().parent
MARK = ROOT / 'data' / '.bofa_reminder_sent'
TZ = ZoneInfo('Europe/Istanbul')


def business_days_between(last: date, end: date) -> int:
    """last'tan sonra, end dahil kaç iş günü (hafta içi) var."""
    n, d = 0, last + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def admin_emails() -> list:
    admins = {u.strip().lower() for u in os.environ.get('ADMIN_USERS', 'hakandeveli').split(',') if u.strip()}
    with sqlite3.connect(str(ROOT / 'data' / 'cache.db')) as c:
        rows = c.execute('SELECT username, email FROM users WHERE active=1 AND email IS NOT NULL AND email != ""').fetchall()
    return [e for u, e in rows if (u or '').lower() in admins]


def main():
    import bofa_db
    st = bofa_db.status()
    if not st.get('last'):
        print('BOFA verisi yok')
        return
    now = datetime.now(TZ)
    end = now.date() if now.hour >= 19 else now.date() - timedelta(days=1)
    last = datetime.strptime(st['last'], '%Y-%m-%d').date()
    lag = business_days_between(last, end)
    print(f'son gün {last}, beklenen {end}, {lag} iş günü geride')
    if lag < REMIND_LAG:
        return
    if not DRY and MARK.exists():
        try:
            prev = datetime.strptime(MARK.read_text().strip(), '%Y-%m-%d').date()
            if (now.date() - prev).days < REMIND_EVERY:
                print('yakın zamanda hatırlatıldı, atlanıyor')
                return
        except ValueError:
            pass

    to = admin_emails()
    if not to:
        print('admin e-posta adresi bulunamadı')
        return
    subject = f'BOFA verisi {lag} iş günü geride — Fintables\'ı açıp senkronu çalıştır'
    body = (f'Merhaba,\n\nSitedeki BOFA (MLB) verisinin son günü: {last:%d.%m.%Y}.\n'
            f'Bugüne göre {lag} iş günü eksik.\n\n'
            'Güncellemek için: Tampermonkey script\'inin kurulu olduğu Chrome penceresinde\n'
            'Fintables\'ı aç (https://fintables.com/araci-kurumlar/MLB/araci-kurum-dagilimi).\n'
            'Sağ alttaki "BOFA senkron" kutusu eksik günleri kendisi doldurur.\n'
            'Kutu bir hata yazarsa: Fintables ve 3nfinans admin oturumlarının açık olduğundan emin ol.\n\n'
            '— 3N Finans otomatik hatırlatma\n')
    if DRY:
        print('[dry] gönderilecek:', to, '|', subject)
        return

    import mailer
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['From'] = mailer.from_header()
    msg['To'] = ', '.join(to)
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain='3nfinans.com')
    s = mailer.smtp_connect()
    try:
        s.sendmail(mailer.envelope_sender(), to, msg.as_string())
    finally:
        try:
            s.quit()
        except Exception:
            pass
    MARK.parent.mkdir(parents=True, exist_ok=True)
    MARK.write_text(now.date().isoformat())
    print('hatırlatma gönderildi:', len(to), 'alıcı')


if __name__ == '__main__':
    main()
