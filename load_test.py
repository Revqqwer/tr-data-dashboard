"""
3N Finans Yük Testi
Kullanım: python load_test.py [kullanici_sayisi] [sure_saniye]
Örnek:    python load_test.py 100 30
"""
import time, threading, sys, statistics
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

BASE_URL = "https://www.3nfinans.com"

PAGES = [
    "/",
    "/dashboard",
    "/api/dth",
    "/api/menkul",
]

results = []
lock = threading.Lock()
stop_flag = threading.Event()


def worker(user_id: int):
    import random
    session_errors = 0
    while not stop_flag.is_set():
        url = BASE_URL + random.choice(PAGES)
        t0 = time.time()
        try:
            req = Request(url, headers={"User-Agent": f"LoadTest/User-{user_id}"})
            with urlopen(req, timeout=10) as r:
                r.read()
            elapsed = (time.time() - t0) * 1000  # ms
            with lock:
                results.append(("ok", elapsed, url))
        except HTTPError as e:
            elapsed = (time.time() - t0) * 1000
            with lock:
                results.append(("err", elapsed, f"{url} → HTTP {e.code}"))
        except (URLError, Exception) as e:
            elapsed = (time.time() - t0) * 1000
            with lock:
                results.append(("err", elapsed, f"{url} → {type(e).__name__}"))
        time.sleep(random.uniform(0.5, 2.0))


def print_stats(duration: int):
    ok   = [r for r in results if r[0] == "ok"]
    err  = [r for r in results if r[0] == "err"]
    total = len(results)

    print("\n" + "═"*55)
    print(f"  3N Finans Yük Testi Sonuçları ({duration}s)")
    print("═"*55)
    print(f"  Toplam istek   : {total}")
    print(f"  Başarılı       : {len(ok)}  ({len(ok)/total*100:.1f}%)")
    print(f"  Hatalı         : {len(err)}  ({len(err)/total*100:.1f}%)")

    if ok:
        times = [r[1] for r in ok]
        print(f"\n  Yanıt süreleri (ms):")
        print(f"    Ortalama  : {statistics.mean(times):.0f} ms")
        print(f"    Medyan    : {statistics.median(times):.0f} ms")
        print(f"    Min       : {min(times):.0f} ms")
        print(f"    Max       : {max(times):.0f} ms")
        print(f"    İstek/sn  : {len(ok)/duration:.1f} req/s")

        # Yanıt süresi dağılımı
        fast   = sum(1 for t in times if t < 500)
        medium = sum(1 for t in times if 500 <= t < 2000)
        slow   = sum(1 for t in times if t >= 2000)
        print(f"\n  Hız dağılımı:")
        print(f"    < 500ms   : {fast} istek  ({fast/len(ok)*100:.0f}%) ✓")
        print(f"    500ms-2s  : {medium} istek  ({medium/len(ok)*100:.0f}%) ⚠")
        print(f"    > 2s      : {slow} istek  ({slow/len(ok)*100:.0f}%) ✗")

    if err:
        print(f"\n  İlk 5 hata:")
        for _, _, msg in err[:5]:
            print(f"    • {msg}")

    print("═"*55)

    # Değerlendirme
    if ok:
        avg = statistics.mean([r[1] for r in ok])
        err_rate = len(err)/total*100
        print("\n  Değerlendirme: ", end="")
        if avg < 500 and err_rate < 5:
            print("🟢 Sunucu yükü kaldırıyor")
        elif avg < 2000 and err_rate < 20:
            print("🟡 Yavaşlama var ama çalışıyor")
        else:
            print("🔴 Sunucu zorlanıyor, optimizasyon gerekebilir")
    print()


if __name__ == "__main__":
    users    = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    print(f"\n  ** Yuk testi basliyor: {users} kullanici, {duration} saniye")
    print(f"  Hedef: {BASE_URL}")
    print("  Ctrl+C ile durdurabilirsiniz\n")

    threads = []
    for i in range(users):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        if (i+1) % 10 == 0:
            print(f"  {i+1} kullanıcı başlatıldı...")
        time.sleep(0.05)  # ramp-up: her 50ms bir kullanıcı

    print(f"\n  ⏱  {duration} saniye test sürüyor...\n")
    try:
        for elapsed in range(duration):
            time.sleep(1)
            with lock:
                cnt = len(results)
            print(f"  {elapsed+1:3d}s — {cnt} istek tamamlandı", end="\r")
    except KeyboardInterrupt:
        print("\n  Test kullanıcı tarafından durduruldu.")
        duration = int(time.time() - time.time())

    stop_flag.set()
    print_stats(duration)
