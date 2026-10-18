# -*- coding: utf-8 -*-
"""
Mağaza uygulaması kişisel bildirimleri — her cihazın takip listesine göre.

  • Hisse: günlük değişim ≥ eşik (varsayılan %3) veya kullanıcının fiyat alarmı (üstü/altı)
  • Fon:   son TEFAS gününde fona ≥ %1 para girişi/çıkışı veya ≥ %2 günlük getiri
  • (Bülten bildirimleri push_daily_brief.py'de, 09:30)

Aynı olay için aynı gün tekrar bildirim gitmez (mobile_watch.last_sent).
PA zamanlanmış görev:  cd ~/tr-data-dashboard && python3 mobile_alerts.py
   (borsa saatlerinde saatlik çalıştırılabilir; tekrarı kendisi engeller)
Deneme:                python3 mobile_alerts.py --dry

Eşik hareketi (günlük ±%N) bildirimleri saatlik çalıştırmada GİTMEZ; yalnızca günde iki kez:
   python3 mobile_alerts.py --moves     # 10:30 ve 18:00 İstanbul (PA/UTC: 07:30 ve 15:00)
Sabah bildirilen aynı yöndeki hareket akşam tekrar gönderilmez.

Haftalık takip listesi özeti (cihaz başına tek bildirim), Pazar 10:00:
   python3 mobile_alerts.py --weekly    # PA günlük 07:00 UTC; Pazar değilse çıkar
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
MOVES = '--moves' in sys.argv   # yalnızca eşik hareketi bildirimleri (PA: 10:30 ve 18:00 İstanbul)
# ABD hisseleri: 10:30'da ABD seansı kapalı → TradingView değişimi bir önceki günün kapanışı ("dün kapanışta").
# 18:00'de ABD seansı yeni açılmış olur, rakam yanıltıcı → akşam çalışmasında ABD hisseleri atlanır.
from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _ZI
_NOW_TR = _dt.now(_ZI('Europe/Istanbul'))
EVENING = _NOW_TR.hour >= 14
# Seans olmayan günlerde fiyat servisi son seansın değişimini göstermeye devam eder → tekrar bildirmemek için:
#   BIST: yalnızca Pzt–Cum;  ABD (sabah, "dün kapanışta"): yalnızca Sal–Cmt (Pzt–Cum seanslarının ertesi sabahı)
BIST_DAY = _NOW_TR.weekday() <= 4
US_MORNING_DAY = 1 <= _NOW_TR.weekday() <= 5
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
                # eşik hareketi yalnızca --moves çalıştırmasında (10:30 ve 18:00); yön anahtarda olduğu için
                # sabah bildirilen aynı yöndeki hareket akşam tekrar gitmez, ters yöne dönerse yeni bildirim olur
                us = not s['symbol'].startswith('BIST:')
                if MOVES and ((not us and BIST_DAY) or (us and not EVENING and US_MORNING_DAY)):
                    when = 'dün kapanışta' if us else 'bugün'
                    title = f'{tk} {when} {_fmt_pct(ch)} {yon}' if us else f'{tk} {_fmt_pct(ch)} {yon}'
                    msgs.append((f'move:{tk}:{"up" if ch > 0 else "down"}', title, f'{tk} {when} {_fmt_pct(ch)} {yon}, fiyat {px}{cur}.'))
            if MOVES:
                continue
            if s.get('above') and d['price'] >= float(s['above']):
                msgs.append((f'above:{tk}:{s["above"]}', f'{tk} hedefine ulaştı', f'{tk} {px}{cur} — alarm seviyen {s["above"]} aşıldı.'))
            if s.get('below') and d['price'] <= float(s['below']):
                msgs.append((f'below:{tk}:{s["below"]}', f'{tk} alarm seviyesinin altında', f'{tk} {px}{cur} — alarm seviyen {s["below"]} altına indi.'))

        for code in ([] if MOVES else fcodes):
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

        # Günlük ve haftalık bülten bildirimleri push_daily_brief.py'den 09:30'da gider (burada yok)

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


WEEKLY_DAY = 6   # haftalık takip listesi özeti: 6 = Pazar (5 = Cumartesi)


def _weekly_perf(symbols: list) -> dict:
    """TradingView'dan haftalık % değişim (Perf.W) — cuma kapanışına göre kesinleşmiş hafta."""
    import requests
    out = {}
    try:
        r = requests.post(mobile_api._TV_SCAN, headers=mobile_api._TV_HEADERS, timeout=15, json={
            'symbols': {'tickers': symbols, 'query': {'types': []}}, 'columns': ['Perf.W']})
        for it in r.json().get('data', []):
            d = it.get('d') or []
            if d and d[0] is not None:
                out[it['s']] = d[0]
    except Exception as e:
        print('haftalık veri alınamadı:', e)
    return out


def run_weekly():
    """Haftada bir, cihaz başına TEK bildirim: takip listesinin haftalık özeti."""
    if _NOW_TR.weekday() != WEEKLY_DAY and '--force' not in sys.argv:
        print('bugün haftalık özet günü değil')
        return
    mobile_api.init_db()
    with sqlite3.connect(mobile_api.DB_PATH) as c:
        devices = c.execute('SELECT token, stocks, last_sent, platform FROM mobile_watch').fetchall()
    syms = sorted({s['symbol'] for d in devices for s in json.loads(d[1] or '[]')})
    perf = _weekly_perf(syms) if syms else {}
    key = 'weekly:' + _NOW_TR.strftime('%G-W%V')
    today = date.today().isoformat()
    sent = 0
    for token, stocks, last_sent, platform in devices:
        last = json.loads(last_sent or '{}')
        if key in last and not DRY:
            continue
        rows = [(s['symbol'].split(':')[-1], perf[s['symbol']]) for s in json.loads(stocks or '[]') if s['symbol'] in perf]
        if not rows:
            continue
        rows.sort(key=lambda x: x[1], reverse=True)
        up = sum(1 for _, v in rows if v > 0)
        sg = lambda v: ('+' if v >= 0 else '−') + _fmt_pct(v)
        if len(rows) == 1:
            tk, v = rows[0]
            body = f'{tk} bu hafta {sg(v)} {"yükseldi" if v >= 0 else "düştü"}.'
        elif up == 0:
            body = (f'{len(rows)} hissenin hepsi düştü. En çok düşen: {rows[-1][0]} {sg(rows[-1][1])} · '
                    f'En az düşen: {rows[0][0]} {sg(rows[0][1])}')
        elif up == len(rows):
            body = (f'{len(rows)} hissenin hepsi yükseldi. En çok yükselen: {rows[0][0]} {sg(rows[0][1])} · '
                    f'En az yükselen: {rows[-1][0]} {sg(rows[-1][1])}')
        else:
            body = (f'{len(rows)} hisseden {up} tanesi yükseldi, {len(rows) - up} tanesi düştü. '
                    f'En iyi: {rows[0][0]} {sg(rows[0][1])} · En kötü: {rows[-1][0]} {sg(rows[-1][1])}')
        title = '📈 Takip listen bu hafta' if up >= len(rows) - up else '📉 Takip listen bu hafta'
        sender = fcm if platform == 'android' else apns
        if DRY:
            print(f'[dry] {token[:10]}… {title} | {body}')
        else:
            res = sender.send(title, body, url='/', tag='haftalik', tokens=[token])
            print(token[:10], body, res)
            last[key] = today
            with sqlite3.connect(mobile_api.DB_PATH) as c:
                c.execute('UPDATE mobile_watch SET last_sent=? WHERE token=?', (json.dumps(last), token))
        sent += 1
    print(f'{len(devices)} cihaz, {sent} haftalık özet')


if __name__ == '__main__':
    run_weekly() if '--weekly' in sys.argv else run()
