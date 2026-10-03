# -*- coding: utf-8 -*-
"""
Mağaza uygulaması kişisel bildirimleri — her cihazın takip listesine göre.

  • Hisse: günlük değişim ≥ eşik (varsayılan %3) veya kullanıcının fiyat alarmı (üstü/altı)
  • Fon:   son TEFAS gününde fona ≥ %1 para girişi/çıkışı veya ≥ %2 günlük getiri
  • Bülten: yeni günlük/haftalık piyasa özeti

Aynı olay için aynı gün tekrar bildirim gitmez (mobile_watch.last_sent).
PA zamanlanmış görev:  cd ~/tr-data-dashboard && python3 mobile_alerts.py
   (borsa saatlerinde saatlik çalıştırılabilir; tekrarı kendisi engeller)
Deneme:                python3 mobile_alerts.py --dry
"""
import json
import sqlite3
import sys
from datetime import date

from dotenv import load_dotenv
load_dotenv()

import apns
import fcm
import mobile_api

DRY = '--dry' in sys.argv
MAX_PER_RUN = 4          # cihaz başına tek çalıştırmada en fazla bildirim


def _fmt_pct(v):
    return f"%{abs(v):.1f}".replace('.', ',')


def _fmt_tl(v):
    a = abs(v)
    if a >= 1e9:
        return f"{a/1e9:.1f} milyar ₺".replace('.', ',')
    if a >= 1e6:
        return f"{a/1e6:.1f} milyon ₺".replace('.', ',')
    return f"{a:,.0f} ₺".replace(',', '.')


def _latest_brief():
    try:
        from tefas_backend.market_agent.reports import get_reports
        r = get_reports(None, 1)
        return r[0] if r else None
    except Exception:
        return None


def run():
    mobile_api.init_db()
    with sqlite3.connect(mobile_api.DB_PATH) as c:
        devices = c.execute('SELECT token, stocks, funds, prefs, last_sent, platform FROM mobile_watch').fetchall()
    if not devices:
        print('takip eden cihaz yok')
        return

    today = date.today().isoformat()
    all_syms = sorted({s['symbol'] for d in devices for s in json.loads(d[1] or '[]')})
    all_funds = sorted({f for d in devices for f in json.loads(d[2] or '[]')})
    q = mobile_api.quotes(all_syms) if all_syms else {}
    funds = {f['code']: f for f in mobile_api.fund_summary(all_funds)} if all_funds else {}
    brief = _latest_brief()

    sent_total = 0
    for token, stocks, fcodes, prefs, last_sent, platform in devices:
        sender = fcm if platform == 'android' else apns
        stocks = json.loads(stocks or '[]')
        fcodes = json.loads(fcodes or '[]')
        prefs = json.loads(prefs or '{}')
        last = json.loads(last_sent or '{}')
        move = float(prefs.get('move_pct') or 3)
        msgs = []   # (anahtar, başlık, metin)

        for s in stocks:
            d = q.get(s['symbol'])
            if not d or d.get('price') is None:
                continue
            tk = s['symbol'].split(':')[-1]
            cur = '₺' if d.get('currency') == 'TRY' else ('$' if d.get('currency') == 'USD' else '')
            px = f"{d['price']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            ch = d.get('change') or 0
            if abs(ch) >= move:
                yon = 'yükseldi' if ch > 0 else 'düştü'
                msgs.append((f'move:{tk}', f'{tk} {_fmt_pct(ch)} {yon}', f'{tk} bugün {_fmt_pct(ch)} {yon}, fiyat {px}{cur}.'))
            if s.get('above') and d['price'] >= float(s['above']):
                msgs.append((f'above:{tk}:{s["above"]}', f'{tk} hedefine ulaştı', f'{tk} {px}{cur} — alarm seviyen {s["above"]} aşıldı.'))
            if s.get('below') and d['price'] <= float(s['below']):
                msgs.append((f'below:{tk}:{s["below"]}', f'{tk} alarm seviyesinin altında', f'{tk} {px}{cur} — alarm seviyen {s["below"]} altına indi.'))

        for code in fcodes:
            f = funds.get(code)
            if not f:
                continue
            fp = f.get('flow_pct_1d')
            if prefs.get('fund_flow', True) and fp is not None and abs(fp) >= 1 and f.get('flow_1d'):
                yon = 'giriş' if f['flow_1d'] > 0 else 'çıkış'
                msgs.append((f'flow:{code}:{f["date"]}', f'{code} fonuna para {yon}i',
                             f'{code}: {f["date"]} günü {_fmt_tl(f["flow_1d"])} net {yon} ({_fmt_pct(fp)}).'))
            r1 = f.get('ret_1d')
            if prefs.get('fund_return', True) and r1 is not None and abs(r1) >= 2:
                yon = 'kazandırdı' if r1 > 0 else 'kaybettirdi'
                msgs.append((f'ret:{code}:{f["date"]}', f'{code} günlük {_fmt_pct(r1)} {yon}',
                             f'{code} fonu {f["date"]} günü {_fmt_pct(r1)} {yon}.'))

        if brief and prefs.get('briefs', True):
            msgs.append((f'brief:{brief["id"]}', 'Piyasa özeti hazır', brief.get('title') or 'Yeni piyasa bülteni yayında.'))

        # aynı gün aynı olay tekrar gönderilmez; bülten ve tarihli fon olayları kalıcı anahtarlıdır
        fresh = [m for m in msgs if last.get(m[0]) != today and not (m[0].startswith(('brief:', 'flow:', 'ret:', 'above:', 'below:')) and m[0] in last)]
        for key, title, body in fresh[:MAX_PER_RUN]:
            if DRY:
                print(f'[dry] {token[:10]}… {title} | {body}')
            else:
                res = sender.send(title, body, url='/', tag='kisisel', tokens=[token])
                print(token[:10], title, res)
            last[key] = today
            sent_total += 1
        if fresh and not DRY:
            with sqlite3.connect(mobile_api.DB_PATH) as c:
                c.execute('UPDATE mobile_watch SET last_sent=? WHERE token=?', (json.dumps(last), token))
    print(f'{len(devices)} cihaz, {sent_total} bildirim')


if __name__ == '__main__':
    run()
