# -*- coding: utf-8 -*-
"""
Push bildirimleri — portfoy uyarisi + kendi yazdigin bildirim.

KULLANIM (PythonAnywhere):

  # B) Portfoy hareketi: son gun degisimi esigi asarsa bildirim yollar
  python push_alerts.py portfolio            # varsayilan esik %2
  python push_alerts.py portfolio --pct 3    # esigi %3 yap
  python push_alerts.py portfolio --dry      # gondermeden sadece hesapla

  # Kendi bildirimin
  python push_alerts.py send "Baslik" "Mesaj govdesi"
  python push_alerts.py send "Baslik" "Mesaj" --url /dashboard#bist-portfoy

  # Kac abone var
  python push_alerts.py count

Portfoy uyarisini gunde bir kez PA scheduled task olarak koyabilirsin
(fiyatlar guncellendikten sonra, or. 16:00 UTC).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env', override=True)

import push  # noqa: E402

PORTFOLIO = ROOT / 'data' / 'portfolio.json'
STATE = ROOT / 'data' / 'push_alert_state.json'


def _fmt_tl(v: float) -> str:
    return f"{v:,.0f}".replace(',', '.') + ' TL'


def portfolio_alert(threshold_pct: float, dry: bool = False):
    """Son iki gunun toplam portfoy degerini karsilastirir; esigi asarsa bildirim."""
    try:
        data = json.loads(PORTFOLIO.read_text(encoding='utf-8'))
        series = data.get('portfolio_daily_value') or []
    except Exception as e:
        print(f'portfolio.json okunamadi: {e}')
        return

    pts = [p for p in series if p.get('total_value')]
    if len(pts) < 2:
        print('Yeterli veri yok (en az 2 gun gerekli).')
        return

    today, prev = pts[-1], pts[-2]
    t_val, p_val = float(today['total_value']), float(prev['total_value'])
    if p_val == 0:
        print('Onceki gun degeri 0, atlaniyor.')
        return

    change = t_val - p_val
    pct = change / p_val * 100.0
    print(f"{prev['date']}: {_fmt_tl(p_val)}  ->  {today['date']}: {_fmt_tl(t_val)}")
    print(f"degisim: {change:+,.0f} TL ({pct:+.2f}%) | esik: +-{threshold_pct}%")

    if abs(pct) < threshold_pct:
        print('Esik asilmadi, bildirim yok.')
        return

    # Ayni gun icin ikinci kez gondermeyi engelle
    try:
        state = json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        state = {}
    if state.get('portfolio_last_date') == today['date']:
        print(f"{today['date']} icin bildirim zaten gonderilmis, atlaniyor.")
        return

    yon = 'yukseldi' if pct > 0 else 'dustu'
    ok = '📈' if pct > 0 else '📉'
    title = f"{ok} Portfoy {abs(pct):.1f}% {yon}"
    body = f"{_fmt_tl(p_val)} → {_fmt_tl(t_val)}  ({change:+,.0f} TL)"

    if dry:
        print(f'[DRY] gonderilecekti: {title} | {body}')
        return

    res = push.send_push(title, body, url='/dashboard#bist-portfoy', tag='portfolio')
    print('sonuc:', res)
    if res.get('ok'):
        state['portfolio_last_date'] = today['date']
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='3N Finans push bildirimleri')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p1 = sub.add_parser('portfolio', help='portfoy hareketi uyarisi')
    p1.add_argument('--pct', type=float, default=2.0, help='esik yuzdesi (varsayilan 2)')
    p1.add_argument('--dry', action='store_true', help='gonderme, sadece hesapla')

    p2 = sub.add_parser('send', help='kendi bildirimini gonder')
    p2.add_argument('title')
    p2.add_argument('body')
    p2.add_argument('--url', default='/', help='tiklayinca acilacak adres')
    p2.add_argument('--tag', default='custom')

    sub.add_parser('count', help='abone sayisi')

    a = ap.parse_args()
    if a.cmd == 'portfolio':
        portfolio_alert(a.pct, a.dry)
    elif a.cmd == 'send':
        print('sonuc:', push.send_push(a.title, a.body, url=a.url, tag=a.tag))
    elif a.cmd == 'count':
        print('abone sayisi:', push.count())


if __name__ == '__main__':
    main()
