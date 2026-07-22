# -*- coding: utf-8 -*-
"""
TradingView WebSocket üzerinden günlük kapanış fiyatı çekme — hafif, bağımsız modül.

parse_portfolio.py bu fonksiyonu barındırıyordu ama o dosya en üstte `pdfplumber`
(PA'da kurulu olmayan) gibi ağır kütüphaneler import ettiği için web yolundan
`from parse_portfolio import _fetch_tv_ws` çağrısı komple çöküyordu. Bu modül yalnız
stdlib + websocket-client kullanır; hem tefas_api (İstatistikler) hem app.py
(canlı fiyat) buradan import eder.
"""
import json

# Fon/holding kodu → BIST sembolü eşlemesi.
# Bazı enstrümanlar portföy dağılımında farklı kodla geçer ama borsada başka
# sembolle işlem görür (TEFAS'ta hiç bulunmazlar). Hem fetch_bist_prices.py hem
# tefas_api.py buradan okur.
#   TPKGYF1 = Aura (eski TERA) Portföy Konut Alfa Katılım Gayrimenkul Yatırım Fonu
#             → BIST'te TPKGY olarak işlem görür.
BIST_ALIASES = {
    "TPKGYF1": "TPKGY",
}


def resolve_bist_symbol(code: str) -> str:
    """Holding kodunu TradingView'da aranacak BIST sembolüne çevirir."""
    c = (code or "").strip().upper()
    return BIST_ALIASES.get(c, c)


def fetch_ohlc(symbol: str, exchange: str = '', n_bars: int = 250) -> dict:
    """
    TradingView'dan günlük OHLC (açılış+kapanış) çeker.
    exchange boş bırakılırsa TradingView sembolü otomatik çözer (US hisseleri/ETF'ler
    için NVDA→NASDAQ, XOM→NYSE, XLE→AMEX gibi). Sadece websocket-client kullanır.
    Döner: {'YYYY-MM-DD': {'o': open, 'c': close}}  (çözülemezse boş dict).
    """
    import websocket as _ws
    import random, string, re
    from datetime import datetime as _dt, timezone as _tz

    def _gen():
        return ''.join(random.choices(string.ascii_lowercase, k=12))

    def _wrap(msg):
        s = json.dumps(msg)
        return f'~m~{len(s)}~m~{s}'

    def _send(ws, func, args):
        ws.send(_wrap({'m': func, 'p': args}))

    def _packets(message):
        for m in re.finditer(r'~m~(\d+)~m~', message):
            length = int(m.group(1))
            yield message[m.end(): m.end() + length]

    chart_sess = 'cs_' + _gen()
    results: dict = {}
    sym = f'{exchange}:{symbol}' if exchange else symbol

    def on_message(ws, message):
        for content in _packets(message):
            if not content.startswith('{'):
                continue
            try:
                obj = json.loads(content)
            except Exception:
                continue
            m = obj.get('m')
            if m in ('symbol_error', 'critical_error', 'protocol_error'):
                ws.close()
                return
            if m == 'timescale_update':
                p = obj.get('p', [])
                if len(p) >= 2 and isinstance(p[1], dict):
                    for series_val in p[1].values():
                        if isinstance(series_val, dict):
                            for bar in series_val.get('s', []):
                                v = bar.get('v', [])
                                if len(v) >= 5:
                                    d = _dt.fromtimestamp(int(v[0]), tz=_tz.utc).date()
                                    results[str(d)] = {'o': round(float(v[1]), 4),
                                                       'c': round(float(v[4]), 4)}
                ws.close()

    def on_open(ws):
        _send(ws, 'set_auth_token', ['unauthorized_user_token'])
        _send(ws, 'chart_create_session', [chart_sess, ''])
        sym_json = json.dumps({'symbol': sym, 'adjustment': 'splits'})
        _send(ws, 'resolve_symbol', [chart_sess, 'sds_sym_1', f'={sym_json}'])
        _send(ws, 'create_series', [chart_sess, 's1', 's1', 'sds_sym_1', 'D', n_bars, ''])

    app = _ws.WebSocketApp(
        'wss://data.tradingview.com/socket.io/websocket',
        header={'Origin': 'https://data.tradingview.com', 'User-Agent': 'Mozilla/5.0'},
        on_message=on_message,
        on_open=on_open,
    )
    app.run_forever(ping_interval=0)
    return results


def _fetch_tv_ws(symbol: str, exchange: str = 'BIST', n_bars: int = 300) -> dict:
    """
    Fetch daily close prices from TradingView via WebSocket.
    Uses only websocket-client (pip install websocket-client) — no tvdatafeed needed.
    Returns {date_str: close_price}.
    """
    import websocket as _ws
    import random, string, re

    def _gen():
        return ''.join(random.choices(string.ascii_lowercase, k=12))

    def _wrap(msg):
        s = json.dumps(msg)
        return f'~m~{len(s)}~m~{s}'

    def _send(ws, func, args):
        ws.send(_wrap({'m': func, 'p': args}))

    def _packets(message):
        for m in re.finditer(r'~m~(\d+)~m~', message):
            length = int(m.group(1))
            yield message[m.end(): m.end() + length]

    chart_sess = 'cs_' + _gen()
    results: dict = {}

    def on_message(ws, message):
        for content in _packets(message):
            if not content.startswith('{'):
                continue
            try:
                obj = json.loads(content)
            except Exception:
                continue
            if obj.get('m') == 'timescale_update':
                p = obj.get('p', [])
                if len(p) >= 2 and isinstance(p[1], dict):
                    for series_val in p[1].values():
                        if isinstance(series_val, dict):
                            for bar in series_val.get('s', []):
                                v = bar.get('v', [])
                                if len(v) >= 5:
                                    from datetime import datetime as _dt, timezone as _tz
                                    d = _dt.fromtimestamp(int(v[0]), tz=_tz.utc).date()
                                    results[str(d)] = round(float(v[4]), 4)
                ws.close()

    def on_open(ws):
        _send(ws, 'set_auth_token', ['unauthorized_user_token'])
        _send(ws, 'chart_create_session', [chart_sess, ''])
        sym_json = json.dumps({'symbol': f'{exchange}:{symbol}', 'adjustment': 'splits'})
        _send(ws, 'resolve_symbol', [chart_sess, 'sds_sym_1', f'={sym_json}'])
        _send(ws, 'create_series', [chart_sess, 's1', 's1', 'sds_sym_1', 'D', n_bars, ''])

    app = _ws.WebSocketApp(
        'wss://data.tradingview.com/socket.io/websocket',
        header={'Origin': 'https://data.tradingview.com', 'User-Agent': 'Mozilla/5.0'},
        on_message=on_message,
        on_open=on_open,
    )
    app.run_forever(ping_interval=0)
    return results
