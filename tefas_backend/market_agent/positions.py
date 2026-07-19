# -*- coding: utf-8 -*-
"""
Market Agent — Tematik Fırsat Getirisi

Her raporun "🎯 TEMATİK FIRSATLAR" (haftalıkta "🏗️ TEMATİK DEEP-DIVE") bölümündeki
her temanın POZİSYON FIRSATLARI kutusundaki TÜM ticker/ETF'leri çıkarır ve raporun
çıktığı günün AÇILIŞ fiyatından long girilseydi bugüne kadarki getiriyi hesaplar.

Sadece long: SHORT / UNDERWEIGHT / AVOID / hedge satırları atlanır.

Bu modül fiyat kaynağından bağımsızdır: `compute_positions` bir `price_lookup(ticker)`
fonksiyonu alır → {'bars': {'YYYY-MM-DD': {'o':.., 'c':..}}} ya da None döndürür.
Böylece hem canlı TradingView hem de test için kullanılabilir.
"""
import re

# ── Ticker olmayan (ama büyük harfli) sık geçen kısaltmalar / etiketler ──────
_STOP = {
    # genel/jargon
    "ETF", "ETFS", "US", "AI", "EV", "EVS", "PE", "IPO", "IPOS", "REIT", "REITS",
    "HBM", "DRAM", "NAND", "DDR", "GPU", "LLM", "SAAS", "TAM", "ARR", "RPO", "SCA",
    "NIM", "RWA", "ROI", "FCF", "EPS", "GLP", "LEO", "DTC", "OEM", "OEMS", "ADR",
    "ADRS", "NSE", "FSD", "DOM", "QSR", "AR", "VR", "TAC", "TACRS",
    "IT", "ALL", "SPAC", "OTC", "SPX", "NDX", "SP", "AUM", "DTH",
    # etiket kelimeleri
    "SHORT", "LONG", "OVERWEIGHT", "UNDERWEIGHT", "AVOID", "HEDGE", "PROXY", "WATCH",
    "PAIR", "TRADE", "SEKTÖR", "SEKTOR", "LONGSHORT",
    # makro
    "DXY", "VIX", "WTI", "OPEC", "CPI", "PCE", "PPI", "PMI", "FED", "FOMC", "BOJ",
    "ECB", "SEC", "FDA", "IEA", "DOJ", "FERC", "NATO", "CHIPS", "IRA", "EM", "DM",
    "USD", "EUR", "JPY", "KRW", "NOK", "CAD", "AUD", "NZD", "CHF", "RUB", "GDP",
    "BOE", "TSY", "GLP1", "FII",
    # yabancı borsa ekleri (parça olarak yakalanırsa)
    "KS", "HK", "NS", "TW", "OL", "AS", "PA", "DE", "TO", "SS", "SW", "SI", "SZ",
    "SA", "NS", "KL", "BO", "MX", "MC",
}
# SMR (NuScale) gerçek bir ticker — stoplistten çıkar
_STOP.discard("SMR")

# Büyük harfli 1-5 harflik token; komşusu harf/rakam/nokta/&/tire olamaz.
# Sınır kontrolü Unicode-duyarlı (\w Türkçe ü/ı/ş… harflerini de kapsar) —
# yoksa "Tüketici" → yanlış "T" tickerı çıkar.
_TICKER_RE = re.compile(r'(?<![\w.&\-])[A-Z]{1,5}(?![\w&])')


def _extract_tickers(s: str) -> list:
    out = []
    for m in _TICKER_RE.findall(s):
        if m in _STOP:
            continue
        if m not in out:
            out.append(m)
    return out


def _split_label(s: str):
    """'│' temizlenmiş satırı (etiket, gövde) olarak böler. Etiket yoksa ('', s)."""
    s = s.replace('**', '')
    if ':' in s:
        head, _, tail = s.partition(':')
        if ',' not in head and len(head) <= 45:
            return head, tail
    return '', s


def _is_bearish(raw: str) -> bool:
    """Satır short/avoid/hedge duruşu mu? (long-only için atlanır)"""
    label, body = _split_label(raw)
    body_np = re.sub(r'\([^)]*\)', '', body)  # parantez açıklamalarını at
    if re.search(r'(short|avoid|hedge|kaçın|çıkış|underweight|bekleme|\bpair\b)', label, re.I):
        return True
    if re.search(r'(\bshort\b|underweight|\bputs?\b|kısa pozisyon|short/|/short|açığa)', body_np, re.I):
        return True
    return False


def _line_tickers(raw: str) -> list:
    _, body = _split_label(raw)
    return _extract_tickers(body)


def _clean_theme_title(s: str) -> str:
    s = re.sub(r'^\W+', '', s, flags=re.UNICODE)   # baştaki emoji/işaretleri at
    return s.strip()


def parse_report_positions(content: str) -> list:
    """
    Döner: [{'theme': str, 'tickers': [str, ...]}, ...]  (long-only, boşlar atılır)
    """
    themes = []
    cur = None
    in_sec = False
    for line in (content or '').split('\n'):
        t = line.strip()
        sec = re.match(r'^━+\s*(.*?)\s*━+$', t)
        if sec:
            in_sec = 'TEMATİK' in sec.group(1).upper()
            if not in_sec:
                cur = None
            continue
        if not in_sec:
            continue
        bold = re.match(r'^\*\*(.+?)\*\*$', t)
        if bold:
            cur = {'theme': _clean_theme_title(bold.group(1)), 'tickers': []}
            themes.append(cur)
            continue
        if cur and t.startswith('│'):
            raw = t.lstrip('│').strip().rstrip('│').strip()
            if not raw or _is_bearish(raw):
                continue
            for tk in _line_tickers(raw):
                if tk not in cur['tickers']:
                    cur['tickers'].append(tk)
    return [th for th in themes if th['tickers']]


def all_tickers(content: str) -> list:
    seen = []
    for th in parse_report_positions(content):
        for tk in th['tickers']:
            if tk not in seen:
                seen.append(tk)
    return seen


def _entry(bars: dict, report_date: str):
    """Rapor gününde (ya da sonraki ilk işlem gününde) açılış fiyatı → (date, open)."""
    for d in sorted(bars):
        if d >= report_date:
            return d, bars[d]['o']
    return None, None


def _current(bars: dict):
    if not bars:
        return None, None
    d = max(bars)
    return d, bars[d]['c']


def compute_positions(report: dict, price_lookup) -> dict:
    """
    report: {'content', 'date' (YYYY-MM-DD), ...}
    price_lookup(ticker) -> {'bars': {date: {'o','c'}}} | None
    Döner: {'computed_at'?, 'avg': float|None, 'count': int, 'themes': [...]}
    """
    report_date = (report.get('date') or (report.get('created_at') or '')[:10]) or ''
    out_themes = []
    all_rets = []
    for th in parse_report_positions(report.get('content', '')):
        rows = []
        for tk in th['tickers']:
            info = price_lookup(tk)
            if not info or not info.get('bars'):
                continue
            bars = info['bars']
            edate, entry = _entry(bars, report_date)
            cdate, cur = _current(bars)
            if entry is None or cur is None or entry <= 0:
                continue
            ret = round((cur - entry) / entry * 100, 2)
            rows.append({
                'ticker': tk, 'entry': round(entry, 2), 'entry_date': edate,
                'current': round(cur, 2), 'current_date': cdate, 'ret': ret,
            })
            all_rets.append(ret)
        if rows:
            avg = round(sum(r['ret'] for r in rows) / len(rows), 2)
            out_themes.append({'theme': th['theme'], 'avg': avg, 'rows': rows})
    avg = round(sum(all_rets) / len(all_rets), 2) if all_rets else None
    return {'avg': avg, 'count': len(all_rets), 'themes': out_themes}
