# -*- coding: utf-8 -*-
"""
BofA (Bank of America) aracı kurum dağılımı — günlük manuel veri deposu.

Veri Fintables'tan elle kopyalanıp admin sayfasına yapıştırılır; burada parse edilip
data/bofa_akd.json'a yazılır. Dosya .gitignore'da — repo PUBLIC olduğu için BIST
lisanslı veri git'e girmemeli, yalnızca PythonAnywhere'de yaşar.

Sayfa yalnızca admin'e açık (ADMIN_SECRET), üyelere gösterilmez.
"""
import json
import os
import re
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_ROOT, 'data', 'bofa_akd.json')

# Fintables sütun sırası (Şirket'ten sonraki sayısal kolonlar)
COLUMNS = [
    'alis_tl', 'alis_lot', 'alis_maliyet',
    'satis_tl', 'satis_lot', 'satis_maliyet',
    'net_tl', 'net_lot', 'net_maliyet',
    'toplam_tl', 'toplam_lot',
]

_TICKER_RE = re.compile(r'^[A-Z][A-Z0-9]{2,5}$')
# "1,88 mr" / "-656,61 mn" / "8.722.205" / "215,34" / "+194,86 mn"
_NUM_RE = re.compile(r'^[+-]?[\d.]+(?:,\d+)?\s*(mr|mn|bn|b)?$', re.I)


def parse_number(raw: str):
    """Türkçe biçimli sayıyı float'a çevirir. 'mr'=milyar, 'mn'=milyon çarpanı."""
    if raw is None:
        return None
    s = str(raw).strip().replace(' ', ' ')
    if not s or s in {'-', '—', '–'}:
        return None

    mult = 1.0
    m = re.search(r'(mr|mn|bn|b)\s*$', s, re.I)
    if m:
        suffix = m.group(1).lower()
        mult = 1_000_000_000.0 if suffix in ('mr', 'bn', 'b') else 1_000_000.0
        s = s[:m.start()].strip()

    s = s.replace(' ', '')
    if not s:
        return None
    # Türkçe biçim: nokta = binlik ayracı, virgül = ondalık
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s) * mult
    except ValueError:
        return None


def _split_cells(line: str) -> list:
    """Satırı hücrelere böler — önce tab, yoksa 2+ boşluk."""
    if '\t' in line:
        cells = line.split('\t')
    else:
        cells = re.split(r'\s{2,}', line)
    return [c.strip() for c in cells if c.strip()]


def parse_table(text: str) -> dict:
    """
    Fintables AKD tablosundan kopyalanan metni satırlara çevirir.
    Döner: {'rows': [...], 'skipped': [ham satır, ...]}
    """
    rows, skipped = [], []
    for line in (text or '').splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        cells = _split_cells(line)
        if len(cells) < 3:
            skipped.append(line.strip())
            continue

        # Ticker'ı bul (başlık satırları, sıra no ve logo metinleri elenir)
        t_idx = next((i for i, c in enumerate(cells) if _TICKER_RE.match(c)), None)
        if t_idx is None:
            skipped.append(line.strip())
            continue

        nums = []
        for c in cells[t_idx + 1:]:
            if _NUM_RE.match(c.replace(' ', '')) or _NUM_RE.match(c):
                v = parse_number(c)
                if v is not None:
                    nums.append(v)

        if len(nums) < 7:   # en azından net_tl'ye kadar gelmeli
            skipped.append(line.strip())
            continue

        row = {'ticker': cells[t_idx]}
        for i, key in enumerate(COLUMNS):
            row[key] = nums[i] if i < len(nums) else None
        rows.append(row)

    return {'rows': rows, 'skipped': skipped}


def _load() -> dict:
    try:
        with open(DATA_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def save_day(date_str: str, rows: list) -> dict:
    """Bir günün satırlarını kaydeder (aynı tarih varsa üzerine yazar)."""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        return {'ok': False, 'error': 'Geçersiz tarih (YYYY-AA-GG olmalı).'}
    if not rows:
        return {'ok': False, 'error': 'Kaydedilecek satır bulunamadı.'}

    data = _load()
    data[date_str] = {
        'rows': rows,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    _save(data)
    return {'ok': True, 'date': date_str, 'count': len(rows)}


def delete_day(date_str: str) -> dict:
    data = _load()
    if date_str in data:
        del data[date_str]
        _save(data)
        return {'ok': True, 'deleted': date_str}
    return {'ok': False, 'error': 'Bu tarih kayıtlı değil.'}


def list_dates() -> list:
    return sorted(_load().keys(), reverse=True)


def get_day(date_str: str) -> dict:
    day = _load().get(date_str)
    if not day:
        return {'date': date_str, 'rows': [], 'updated_at': None, 'summary': {}}
    rows = day.get('rows', [])
    return {
        'date': date_str,
        'rows': rows,
        'updated_at': day.get('updated_at'),
        'summary': summarize(rows),
    }


def summarize(rows: list) -> dict:
    """
    Günün özeti:
      - net_total_tl : BofA'nın o gün nette ne kadar alıp sattığı (+ = net alıcı)
      - bought / sold: aldığı ve sattığı hisseler, net büyüklüğe göre sıralı
    """
    net = [r for r in rows if isinstance(r.get('net_tl'), (int, float))]

    bought = sorted([r for r in net if r['net_tl'] > 0], key=lambda r: -r['net_tl'])
    sold = sorted([r for r in net if r['net_tl'] < 0], key=lambda r: r['net_tl'])

    buy_total = sum(r['net_tl'] for r in bought)
    sell_total = sum(r['net_tl'] for r in sold)          # negatif
    gross_buy = sum(r.get('alis_tl') or 0 for r in rows)
    gross_sell = sum(r.get('satis_tl') or 0 for r in rows)

    def slim(r):
        return {
            'ticker': r['ticker'],
            'net_tl': r['net_tl'],
            'net_lot': r.get('net_lot'),
            'net_maliyet': r.get('net_maliyet'),
        }

    return {
        'net_total_tl': buy_total + sell_total,   # + net alıcı, - net satıcı
        'buy_total_tl': buy_total,
        'sell_total_tl': sell_total,
        'gross_buy_tl': gross_buy,
        'gross_sell_tl': gross_sell,
        'stock_count': len(rows),
        'bought_count': len(bought),
        'sold_count': len(sold),
        'bought': [slim(r) for r in bought],
        'sold': [slim(r) for r in sold],
    }


def net_history(ticker: str) -> list:
    """Bir hissenin gün gün net TL serisi (grafik için)."""
    tk = (ticker or '').strip().upper()
    out = []
    for date_str, day in sorted(_load().items()):
        for r in day.get('rows', []):
            if r.get('ticker') == tk and isinstance(r.get('net_tl'), (int, float)):
                out.append({'date': date_str, 'net_tl': r['net_tl'], 'net_lot': r.get('net_lot')})
                break
    return out
