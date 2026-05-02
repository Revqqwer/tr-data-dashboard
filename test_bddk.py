"""
BDDK Haftalık Bülten Scraper Testi
Çalıştır: python test_bddk.py
"""
import requests
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings('ignore')

URL = 'https://www.bddk.org.tr/bultenhaftalik'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def test_bddk():
    print("BDDK sayfası çekiliyor...")
    r = requests.get(URL, headers=HEADERS, timeout=30, verify=False)
    print(f"HTTP Status: {r.status_code}")
    print(f"İçerik boyutu: {len(r.text)} karakter")
    print()

    soup = BeautifulSoup(r.text, 'html.parser')

    # 1. Sayfadaki tarihi bul
    print("=== TARİH ===")
    for tag in soup.find_all(string=lambda t: t and ('2026' in t or 'Nisan' in t or 'Mart' in t)):
        txt = tag.strip()
        if len(txt) < 60 and txt:
            print(f"  > {txt}")

    print()

    # 2. Tüm tabloları bul
    tables = soup.find_all('table')
    print(f"=== TABLOLAR: {len(tables)} adet ===")

    # 3. İstediğimiz satırları ara
    keywords = [
        'tüketici', 'tuketici',
        'ticari', 'bireysel kredi kartı', 'bireysel kredi karti'
    ]

    print()
    print("=== İLGİLİ SATIRLAR ===")
    found = False
    for i, table in enumerate(tables):
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if not cells:
                continue
            row_text = ' '.join(c.get_text(strip=True) for c in cells).lower()
            if any(kw in row_text for kw in keywords):
                print(f"\n[Tablo {i}] {' | '.join(c.get_text(strip=True) for c in cells)}")
                found = True

    if not found:
        print("Hiç bulunamadı — sayfa JS ile render ediliyor olabilir.")
        print()
        print("=== İLK 3000 KARAKTER (ham HTML) ===")
        print(r.text[:3000])

if __name__ == '__main__':
    test_bddk()
