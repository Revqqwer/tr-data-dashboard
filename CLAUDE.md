# TRData Dashboard — Proje Rehberi

## Genel Bakış

**TRData**, iki iç içe uygulamadan oluşan bir Türkiye ekonomi + fon akışı panosu:

1. **Ana Uygulama (Flask)** — Türkiye makro verileri (DTH, Menkul Kıymet, Krediler, Bütçe, Ödemeler Dengesi, Turizm, Konut, Enflasyon, Makro Tahmin)
2. **TEFAS Flow (React + Vite)** — TEFAS yatırım fonu para akışları + ABD kripto ETF akışları. `/tefas/` yolunda iframe olarak ana uygulamaya gömülü.

---

## Repo & Deployment

| Bilgi | Değer |
|-------|-------|
| GitHub | `https://github.com/Revqqwer/tr-data-dashboard` |
| Üretim sunucusu | PythonAnywhere — kullanıcı: `hakandeveli24` |
| Üretim URL | `hakandeveli24.pythonanywhere.com` |
| WSGI dosyası | PA panelinden reload edilir |

**Deploy süreci:**
```bash
# 1. Local: frontend build + copy
cd C:\Users\hakan\OneDrive\Masaüstü\tr-data-dashboard
python build_tefas.py          # React build → tefas_build/ klasörüne kopyalar

# 2. Git push
git add -A && git commit -m "açıklama" && git push

# 3. PythonAnywhere
cd ~/tr-data-dashboard && git pull
# → Web sekmesinden yeşil Reload butonuna bas
```

---

## Proje Klasör Yapısı

```
tr-data-dashboard/           ← Ana repo (Python/Flask)
├── app.py                   ← Flask ana uygulama, tüm ekonomi API'leri + auth
├── tefas_api.py             ← TEFAS Blueprint: /api/leaderboard, /api/flow/*, /api/crypto/*
├── build_tefas.py           ← Frontend build edip tefas_build/'a kopyalar
├── daily_collect.py         ← Günlük TEFAS verisi çekme (PA scheduled task, 17:00 UTC)
├── daily_crypto_collect.py  ← Günlük Kripto ETF verisi çekme (PA scheduled task, 18:00 UTC)
├── templates/
│   ├── index.html           ← Ana TRData sayfa: koyu sidebar + iframe
│   ├── login.html
│   ├── register.html
│   └── admin.html
├── static/
│   ├── css/style.css
│   └── js/main.js           ← Sayfa geçişleri, Chart.js grafikleri
├── tefas_build/             ← React build çıktısı (git'te var, PA'ya deploy edilir)
├── tefas_backend/
│   ├── database.py          ← SQLModel ORM modelleri + engine
│   ├── collector.py         ← TEFAS API'den veri çekme
│   ├── crypto_collector.py  ← farside.co.uk scraper + Excel importer
│   └── flow_analysis.py     ← Net akış hesaplama mantığı
└── data/
    └── tefas.db             ← SQLite veritabanı

tefas-flow/                  ← React frontend kaynak kodu (ayrı klasör)
└── frontend/
    └── src/
        ├── App.tsx           ← Top navbar + routes
        ├── theme.ts          ← Tasarım sistemi (renkler, spacing)
        ├── pages/
        │   ├── Leaderboard.tsx   ← En fazla giriş/çıkış fon tablosu
        │   ├── FlowExplorer.tsx  ← Kategori bazlı akış analizi
        │   ├── FundDetail.tsx    ← Fon arama + tarihsel grafik
        │   └── CryptoFlow.tsx    ← BTC/ETH ETF akış grafiği
        └── components/
            ├── FundTable.tsx
            └── FlowChart.tsx
```

---

## Veritabanı Modelleri (`tefas_backend/database.py`)

| Tablo | Açıklama |
|-------|----------|
| `fund_daily` | Ham günlük fon verisi (fiyat, AUM, pay sayısı, yatırımcı) |
| `fund_flow` | Hesaplanmış net akış: `(shares_t - shares_t-1) × price_t` |
| `fund_composition` | Günlük portföy dağılımı |
| `fund_meta` | Fon metadata (kategori, tip, isim) |
| `crypto_etf_flow` | BTC/ETH spot ETF günlük akışları ($M, farside.co.uk) |

---

## API Endpoint'leri

### TEFAS Blueprint (`tefas_api.py`)
```
GET  /api/flow/available-dates          → Mevcut tarih listesi
GET  /api/leaderboard                   → En fazla giriş/çıkış fonlar
       ?date=2026-05-13
       ?start=2026-05-01&end=2026-05-13
       ?limit=20 &fund_type=YAT
GET  /api/flow/asset-class              → Fon tipi bazlı akış özeti
GET  /api/flow/asset-class/detail       → Kategori detayı
GET  /api/flow/asset-class/contributors → Kategoriye katkı yapan fonlar
GET  /api/funds                         → Fon listesi (arama için)
GET  /api/funds/<code>/flow             → Tek fon tarihsel akış
GET  /api/funds/<code>/composition      → Portföy dağılımı
GET  /api/crypto/flows                  → BTC/ETH ETF akış verisi
       ?asset=BTC&days=90
       ?asset=ETH&start=2024-01-01&end=2024-12-31
POST /api/crypto/collect                → farside.co.uk'tan güncel veri çek
POST /api/crypto/import-excel           → Excel'den tarihi veri yükle
```

### Ana Flask (`app.py`)
```
GET  /api/dth           → Döviz Tevdiat Hesapları
GET  /api/menkul        → Menkul Kıymet akımları
GET  /api/credit        → Kredi verileri
GET  /api/credit-detail → Kredi alt kalemleri
GET  /api/butce         → Bütçe dengesi
GET  /api/dis-ticaret   → Dış ticaret
GET  /api/odeme-dengesi → Ödemeler dengesi
GET  /api/turizm        → Turizm istatistikleri
GET  /api/konut         → Konut piyasası
GET  /api/enflasyon     → TÜFE verileri
GET  /api/makro         → Makro tahmin tablosu
```

---

## TEFAS Flow Sayfaları

| Sayfa | Route | Açıklama |
|-------|-------|----------|
| Liderboard | `/` | Günlük/dönemsel en fazla giriş-çıkış fonlar |
| Akış | `/flow` | Kategori bazlı (YAT/EMK/BYF) akış analizi |
| Fon Ara | `/funds` | Fon arama, tarihsel grafik, portföy dağılımı |
| Kripto ETF | `/crypto` | BTC/ETH ABD spot ETF akışları (farside.co.uk) |

---

## TRData Sidebar Sayfaları (`templates/index.html`)

`data-page` değeri ile `switchPage()` fonksiyonu sayfayı açar. Her sayfanın:
- `id="topbar-{page}"` → başlık barı
- `id="page-{page}"` → içerik div'i
vardır.

TEFAS (`data-page="tefas"`) ve Kripto ETF (`data-page="kripto"`) sayfaları iframe ile açılır:
- TEFAS → `<iframe src="/tefas/">`
- Kripto ETF → `<iframe src="/tefas/crypto">`

---

## PythonAnywhere Scheduled Tasks

| Görev | Komut | Zaman |
|-------|-------|-------|
| TEFAS günlük veri | `cd ~/tr-data-dashboard && python daily_collect.py` | 17:00 UTC |
| Kripto ETF günlük veri | `cd ~/tr-data-dashboard && python daily_crypto_collect.py` | 18:00 UTC |

---

## Kripto ETF Modülü

- **Kaynak:** farside.co.uk (BTC: `/btc/`, ETH: `/eth/`)
- **Cloudflare bypass:** `cloudscraper` kütüphanesi kullanılıyor
- **BTC tickerları:** IBIT, FBTC, BITB, ARKB, BTCO, EZBC, BRRR, HODL, BTCW, MSBT, GBTC, BTC
- **ETH tickerları:** ETHA, FETH, ETHW, CETH, ETHV, QETH, EZET, ETH, ETHE
- **Tarihi veri:** `data/btcethflow.xlsx` Excel dosyasından yüklendi (5808 BTC + 3510 ETH kayıt)

---

## Frontend Build Süreci

```bash
# 1. Frontend kaynak: C:\Users\hakan\OneDrive\Masaüstü\tefas-flow\frontend\
# 2. Build:
cd C:\Users\hakan\OneDrive\Masaüstü\tr-data-dashboard
python build_tefas.py
# → npm run build çalıştırır
# → dist/ → tefas_build/ kopyalanır
# 3. Git push ile PA'ya deploy edilir
```

---

## Auth Sistemi

- Session-based login (`flask_session`)
- Davet kodu ile kayıt (`/register?code=3N-XXXXXX`)
- Admin panel: `/admin/3n-admin-gizli`
- Tüm `/api/*` endpoint'leri `_auth()` kontrolü yapar

---

## Önemli Teknik Kararlar

- **SQLite** (MVP) — `data/tefas.db`, 800+ fon × 365 gün ≈ 300K satır
- **Flow formülü:** `net_flow = (shares_t - shares_{t-1}) × price_t`
- **Fon tipleri:** YAT (Yatırım), EMK (Emeklilik), BYF (Borsa Yatırım Fonu)
- **React SPA** Flask üzerinde serve edilir (`/tefas/` prefix, tüm route'lar `index.html`'e düşer)
- **cloudscraper** — farside.co.uk Cloudflare engeli için
- **TEFAS API:** `POST https://www.tefas.gov.tr/api/DB/BindHistoryInfo`

---

## Sık Kullanılan Komutlar (PythonAnywhere)

```bash
# Güncel veri çek
cd ~/tr-data-dashboard && python daily_collect.py

# Eksik gün backfill
python -c "
from tefas_backend.collector import collect_range
import datetime
collect_range(datetime.date(2026,5,1), datetime.date(2026,5,13))
"

# Kripto verisi çek
python daily_crypto_collect.py

# DB init (tablo oluştur)
python -c "from tefas_backend.database import init_db; init_db()"
```
