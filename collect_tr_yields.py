"""
TR 2Y ve 10Y tahvil faizi tarihsel verisi — tvDatafeed kullanarak TradingView'dan çeker.

Kullanım (PythonAnywhere bash):
    pip3.10 install --user tvDatafeed
    cd ~/tr-data-dashboard && python3.10 collect_tr_yields.py

İlk çalıştırmada 10 yıl (120 bar) geçmişi çeker ve tr_yield_cache tablosunu doldurur.
Sonraki çalıştırmalarda mevcut kayıtları günceller (UPSERT).
"""
import os
import sqlite3
import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'cache.db')

SYMBOLS = [
    ('TR02Y', 'TVC', 'tr2y'),
    ('TR10Y', 'TVC', 'tr10y'),
]

N_BARS = 120  # ~10 yıl aylık veri


def ensure_table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS tr_yield_cache (
        ym    TEXT PRIMARY KEY,
        tr2y  REAL,
        tr10y REAL
    )''')
    conn.commit()


def collect():
    try:
        from tvDatafeed import TvDatafeed, Interval
    except ImportError:
        print("tvDatafeed kurulu değil. Şu komutu çalıştır:")
        print("  pip3.10 install --user tvDatafeed")
        return

    tv = TvDatafeed()

    all_data: dict[str, dict] = {}  # ym -> {tr2y: ..., tr10y: ...}

    for symbol, exchange, col in SYMBOLS:
        print(f"Çekiliyor: {exchange}:{symbol} …", flush=True)
        try:
            df = tv.get_hist(symbol, exchange, interval=Interval.in_monthly, n_bars=N_BARS)
            if df is None or df.empty:
                print(f"  → Veri boş: {symbol}")
                continue
            df = df.reset_index()
            count = 0
            for _, row in df.iterrows():
                dt = row.get('datetime') or row.get('index')
                close = row.get('close')
                if close is None or (hasattr(close, '__class__') and str(close) == 'nan'):
                    continue
                if hasattr(dt, 'strftime'):
                    ym = dt.strftime('%Y-%m')
                else:
                    ym = str(dt)[:7]
                all_data.setdefault(ym, {})[col] = round(float(close), 2)
                count += 1
            print(f"  → {count} ay")
        except Exception as e:
            print(f"  → Hata: {e}")

    if not all_data:
        print("Hiç veri çekilemedi.")
        return

    with sqlite3.connect(DB_PATH) as conn:
        ensure_table(conn)
        upserted = 0
        for ym, vals in sorted(all_data.items()):
            existing = conn.execute(
                'SELECT tr2y, tr10y FROM tr_yield_cache WHERE ym=?', (ym,)
            ).fetchone()
            if existing:
                new_tr2y  = vals.get('tr2y',  existing[0])
                new_tr10y = vals.get('tr10y', existing[1])
                conn.execute(
                    'UPDATE tr_yield_cache SET tr2y=?, tr10y=? WHERE ym=?',
                    (new_tr2y, new_tr10y, ym)
                )
            else:
                conn.execute(
                    'INSERT INTO tr_yield_cache(ym, tr2y, tr10y) VALUES(?,?,?)',
                    (ym, vals.get('tr2y'), vals.get('tr10y'))
                )
            upserted += 1
        conn.commit()

    print(f"\nTamamlandı: {upserted} ay kaydedildi → {DB_PATH}")

    # Özet: son 6 ay
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            'SELECT ym, tr2y, tr10y FROM tr_yield_cache ORDER BY ym DESC LIMIT 6'
        ).fetchall()
    print("\nSon 6 ay:")
    print(f"{'Ay':<10} {'TR2Y':>8} {'TR10Y':>8}")
    for ym, tr2y, tr10y in rows:
        print(f"{ym:<10} {(tr2y or '-'):>8} {(tr10y or '-'):>8}")


if __name__ == '__main__':
    collect()
