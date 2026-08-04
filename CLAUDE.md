# TRData Dashboard — Proje Rehberi

## Genel Bakış

**3N Finans** (repo adı: TRData), iç içe geçmiş parçalardan oluşan bir Türkiye ekonomi +
fon akışı panosu — üretimde **www.3nfinans.com**:

1. **Ana Uygulama (Flask)** — Türkiye makro verileri (DTH, Menkul Kıymet, Krediler, Bütçe,
   Ödemeler Dengesi, Turizm, Konut, Enflasyon, Makro Tahmin), auth, admin, e-posta bülteni,
   PWA + web push, çarkıfelek.
2. **TEFAS Flow (React + Vite)** — Fon para akışları, Özel Fonlar, kripto ETF akışları,
   BIST portföy takibi. `/tefas/` yolunda **iframe** olarak gömülü.
3. **Statik blueprint sayfaları** — BİST endeksleri (`/bist/`) ve ABD endeksleri (`/usa/`),
   düz HTML + kendi API'leri.

---

## Repo & Deployment

| Bilgi | Değer |
|-------|-------|
| GitHub | `https://github.com/Revqqwer/tr-data-dashboard` |
| Üretim sunucusu | PythonAnywhere — kullanıcı: `hakandeveli24` |
| Üretim URL | `https://www.3nfinans.com` (PA: `hakandeveli24.pythonanywhere.com`) |
| Yerel repo | `C:\Users\hakan\OneDrive\Masaüstü\.claude\tr-data-dashboard` |
| React kaynağı | `C:\Users\hakan\OneDrive\Masaüstü\.claude\tefas-flow\frontend` (ayrı klasör, git'te değil) |
| DNS / CDN | Cloudflare (robots.txt'e AI-bot bloklarını Cloudflare enjekte eder) |
| WSGI dosyası | PA panelinden reload edilir |

**Deploy süreci:**
```bash
# 1. Local — SADECE React değiştiyse build gerekir
cd C:\Users\hakan\OneDrive\Masaüstü\.claude\tr-data-dashboard
python build_tefas.py          # React build → tefas_build/ klasörüne kopyalar

# 2. Git push
git add -A && git commit -m "açıklama" && git push

# 3. PythonAnywhere (Consoles → Bash)
cd ~/tr-data-dashboard && git checkout -- data/portfolio.json && git pull
# → Web sekmesinden yeşil Reload butonuna bas
```

> **`git checkout -- data/portfolio.json` şart:** PA'da `update_portfolio.py` bu dosyayı
> yerel olarak değiştiriyor; temizlenmezse `git pull` çakışır.

**Deploy gerektirmeyen dosyalar** (gitignore'da, PA'da yaşar):
`.env`, `data/portfolio_overrides.json`, veritabanları (`*.db`). Bunları PA'da
doğrudan düzenle — admin paneli zaten override dosyasını oraya yazar.

---

## Proje Klasör Yapısı

```
tr-data-dashboard/           ← Ana repo (Python/Flask)
├── app.py                   ← Flask ana uygulama: ekonomi API'leri, auth, admin, push, çarkıfelek
├── tefas_api.py             ← TEFAS Blueprint: /api/leaderboard, /api/flow/*, /api/crypto/*,
│                              /api/custom-funds/*  (Özel Fonlar)
├── bist_api.py              ← BIST Blueprint: /bist/ sayfaları + /bist/api/* (endeks getiri)
├── usa_api.py               ← ABD endeksleri Blueprint: /usa/
├── mailer.py                ← SMTP sağlayıcısı env ile değişir (Brevo relay)
├── push.py                  ← Web Push abonelik deposu + gönderim (VAPID)
├── push_alerts.py           ← Portföy/özel bildirim tetikleyicileri
├── gen_vapid.py             ← VAPID anahtar üretimi (bir kez)
├── wheel.py                 ← Çarkıfelek hisse öneri deposu (üye başı 5, admin sıfırlama)
├── parse_portfolio.py       ← Pusula PDF → data/portfolio.json (BIST portföy)
├── update_portfolio.py      ← PA'da portfolio.json'u günceller (bu yüzden deploy'da checkout gerekir)
├── build_tefas.py           ← React build edip tefas_build/'a kopyalar
├── daily_collect.py         ← Günlük TEFAS verisi (PA scheduled task)
├── daily_crypto_collect.py  ← Günlük Kripto ETF verisi (PA scheduled task)
├── collect_bist.py          ← BIST endeks geçmişi (TradingView WS; günlük 252 + haftalık 520 bar)
├── collect_usa.py           ← ABD endeks verisi
├── templates/
│   ├── index.html           ← Ana panel: koyu sidebar + iframe'ler + OG meta
│   ├── landing.html         ← Giriş yapmamış ziyaretçi sayfası
│   ├── login.html / register.html / profile.html
│   ├── forgot_password.html / reset_password.html
│   ├── admin.html           ← Admin paneli (davet kodu, kullanıcılar, push, çarkıfelek, makro)
│   ├── admin_portfolio.html ← Portföy Manuel Düzenleme (ayrı sayfa)
│   └── carkifelek.html      ← Çarkıfelek — Hisse Analizi (tek dosya, SVG çark)
├── static/
│   ├── css/style.css        ← Ana tema + dark mode (:root[data-theme="dark"])
│   ├── js/main.js           ← Sayfa geçişleri, Chart.js grafikleri
│   ├── sw.js                ← Service worker (PWA + web push)
│   ├── manifest.json        ← PWA manifesti
│   ├── og-image-v3.png      ← Sosyal medya paylaşım kartı (1200×630)
│   └── icons/
├── bist_static/             ← BIST endeks sayfaları (statik HTML: index, karsilastirma)
├── usa_static/              ← ABD endeks sayfaları
├── tefas_build/             ← React build çıktısı (git'te var, PA'ya deploy edilir)
├── tefas_backend/
│   ├── database.py          ← SQLModel ORM modelleri + engine
│   ├── collector.py         ← TEFAS API'den veri çekme
│   ├── crypto_collector.py  ← farside.co.uk scraper + Excel importer
│   ├── flow_analysis.py     ← Net akış hesaplama mantığı
│   └── market_agent/        ← Günlük piyasa özeti (rapor + e-posta + push)
└── data/
    ├── tefas.db             ← Fon verisi (SQLite)
    ├── cache.db             ← Kullanıcılar, davet kodları, push abonelikleri, çarkıfelek
    ├── bist_cache.db        ← BIST endeks geçmişi   · usa_cache.db ← ABD endeksleri
    ├── portfolio.json       ← PDF'ten üretilen BIST portföyü (PA'da yerel değişir)
    ├── portfolio_overrides.json ← Manuel düzeltmeler (gitignore, PA'da yaşar)
    └── fund_holdings.json   ← Özel fon içerik ağırlıkları (TLY/PBR/PHE)

../tefas-flow/frontend/      ← React kaynak kodu (AYRI klasör, bu repo'da DEĞİL)
└── src/
    ├── App.tsx              ← Navbar + routes
    ├── theme.ts             ← Tasarım sistemi (renkler, spacing)
    └── pages/
        ├── Leaderboard.tsx      ← En fazla giriş/çıkış fonlar
        ├── FlowExplorer.tsx     ← Kategori bazlı akış analizi
        ├── FundDetail.tsx       ← Fon arama + tarihsel grafik
        ├── CustomFunds.tsx      ← Özel Fonlar (4 grafik: akış, getiri, yatırımcı, AUM)
        ├── CryptoFlow.tsx       ← BTC/ETH ETF akışı
        ├── BistPortfolio.tsx    ← BIST portföy takibi
        └── GlobalMarket.tsx     ← Global piyasa
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

# Özel Fonlar
GET  /api/custom-funds/flow             → 4 seri: flow (kümülatif akış), ret (getiri),
       ?days=30 | ?start=&end=            inv (yatırımcı), aum (fon büyüklüğü) — her biri
                                          fon bazında + TOPLAM
GET  /api/custom-funds/<code>/holdings  → Fon içi hisse dağılımı + canlı getiri katkısı
                                          (ağırlıklar data/fund_holdings.json'dan)
```

### BIST / ABD Blueprint'leri
```
GET  /bist/  ·  /bist/karsilastirma     → Statik endeks sayfaları
GET  /bist/api/indices                  → Endeks listesi
GET  /bist/api/history?period=1y        → Dönem: 1h,1a,3a,6a,1y,3y,5y,10y
GET  /bist/api/history/custom           → Özel tarih aralığı
GET  /bist/api/returns-summary          → Tüm dönemler için getiri özeti
GET  /usa/                              → ABD endeksleri sayfası
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
GET  /api/portfolio     → BIST portföyü (portfolio.json + override'lar uygulanmış)

# PWA / Web Push
GET  /api/push/public-key      → VAPID açık anahtarı
POST /api/push/subscribe       → Abonelik kaydı   · POST /api/push/unsubscribe

# Çarkıfelek (giriş gerekli)
GET  /carkifelek               → Çark sayfası
GET  /api/wheel/suggestions    → Öneriler + kalan hak
POST /api/wheel/suggest        → Hisse öner (üye başı max 5)  · POST /api/wheel/unsuggest

# Admin (secret = ADMIN_SECRET)
GET  /admin/<secret>                    → Ana panel
GET  /admin/<secret>/portfoy            → Portföy Manuel Düzenleme
POST /admin/<secret>/push/send          → Tüm abonelere bildirim
POST /admin/<secret>/wheel/reset        → Çarkıfelek önerilerini sıfırla
POST /admin/<secret>/portfolio-override → Pozisyon override / kapatma / nakit / NSP
```

---

## TEFAS Flow Sayfaları

React SPA `/tefas/` altında servis edilir (Vite `base: "/tefas/"`), route'lar client-side.

| Sayfa | Route | Açıklama |
|-------|-------|----------|
| Liderboard | `/` | Günlük/dönemsel en fazla giriş-çıkış fonlar |
| Akış | `/flow` | Kategori bazlı (YAT/EMK/BYF) akış analizi |
| Fon Ara | `/funds` | Fon arama, tarihsel grafik, portföy dağılımı |
| Özel Fonlar | `/custom` | Seçili fonlar — 4 grafik: kümülatif akış, getiri, yatırımcı sayısı, **fon büyüklüğü (AUM)** |
| İstatistikler | `/stats` | Genel istatistikler |
| Kripto ETF | `/crypto` | BTC/ETH ABD spot ETF akışları (farside.co.uk) |
| BIST Portföy | `/bist` | Hisse portföy takibi, gerçekleşmiş K/Z, açık pozisyonlar |
| Global Piyasa | `/global` | Global piyasa takibi |

---

## TRData Sidebar Sayfaları (`templates/index.html`)

`data-page` değeri ile `switchPage()` fonksiyonu sayfayı açar. Her sayfanın:
- `id="topbar-{page}"` → başlık barı
- `id="page-{page}"` → içerik div'i
vardır.

Bazı sayfalar iframe ile açılır:
- TEFAS → `<iframe src="/tefas/">` · Kripto ETF → `/tefas/crypto`
- BIST Portföy → `/tefas/bist` · Global Piyasa → `/tefas/global`
- BİST Endeksleri → `/bist/` ve `/bist/karsilastirma` (statik HTML)
- ABD Endeksleri → `/usa/`

**Çarkıfelek** sidebar'da BIST Portföy'ün altında bir alt-öğedir; iframe değil,
`/carkifelek` adresini **yeni sekmede** açar.

---

## PythonAnywhere Scheduled Tasks

> Saatler zaman zaman elle değiştirildi — **güncel hali için PA → Tasks sekmesine bak**,
> buradaki değerleri tek doğru kaynak sayma.

| Görev | Komut |
|-------|-------|
| TEFAS günlük veri | `cd ~/tr-data-dashboard && python daily_collect.py` |
| Kripto ETF günlük veri | `cd ~/tr-data-dashboard && python daily_crypto_collect.py` |
| BIST endeks geçmişi | `cd ~/tr-data-dashboard && python collect_bist.py` |
| ABD endeksleri | `cd ~/tr-data-dashboard && python collect_usa.py` |
| Günlük piyasa özeti | `cd ~/tr-data-dashboard && python -m tefas_backend.market_agent.run run_daily` |

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
# Frontend kaynak: C:\Users\hakan\OneDrive\Masaüstü\.claude\tefas-flow\frontend\
cd C:\Users\hakan\OneDrive\Masaüstü\.claude\tr-data-dashboard
python build_tefas.py
# → npm run build (tsc + vite) çalıştırır
# → dist/ → tefas_build/ kopyalanır  →  git push ile PA'ya gider
```

- React kaynağı **bu repo'da değil**, kardeş `../tefas-flow/` klasöründe; sadece build
  çıktısı (`tefas_build/`) commit'lenir. Yani `.tsx` düzenledikten sonra **build almadan
  push edersen üretimde hiçbir şey değişmez.**
- `build_tefas.py` içinde `tsc` çalıştığı için başarılı build = tip kontrolü geçti demektir.

---

## Auth Sistemi

- **Oturum:** Flask'ın yerleşik imzalı cookie session'ı (`flask_session` eklentisi
  KULLANILMIYOR — sunucuda oturum saklanmaz, bu yüzden PA reload'ı kimseyi atmaz).
- **Kalıcı oturum:** `PERMANENT_SESSION_LIFETIME = 365 gün` + `SESSION_REFRESH_EACH_REQUEST`
  → her istekte süre uzar. Kullanıcı **"Çıkış"a basmadıkça** düşmez; tarayıcı/PC kapansa
  da kalır. Cookie: `HttpOnly` + `SameSite=Lax` + `Secure`
  (yerelde http ile test için `.env`'e `SESSION_COOKIE_SECURE=0`).
- **Kayıt anında açılır — mail onayı YOK, davet kodu YOK.** `/register` email +
  kullanıcı adı + şifre alır, `users.active` varsayılanı `1`, `INSERT`'ten hemen sonra
  oturum açılıp `/dashboard`'a yönlendirir. (Davet kodu sistemi tabloda duruyor ama
  kayıt akışı artık kod istemiyor.)
- **Giriş şartı:** `SELECT ... WHERE username=? AND active=1`. Bir üyeyi engellemenin
  tek yolu admin panelinden pasife almaktır (`active=0`).
- **Admin paneli:** `/admin/<ADMIN_SECRET>` — gizli anahtar `.env`'deki `ADMIN_SECRET`'ten
  gelir; **tanımlı değilse** kod içi varsayılan `3n-admin-gizli` kullanılır.
  Portföy düzenleme ayrı sayfada: `/admin/<ADMIN_SECRET>/portfoy`.
- **`SECRET_KEY`** `.env`'den okunur; yoksa repo'daki sabit yedeğe düşer (güvensiz —
  üretimde mutlaka `.env`'de tanımlı olmalı). Değiştirilirse **tüm oturumlar düşer**.
- Tüm `/api/*` endpoint'leri `_auth()` kontrolü yapar; `_require_login` before_request'i
  public olmayan sayfaları `/login`'e yönlendirir (`_PUBLIC_EXACT` / `_PUBLIC_PREFIXES`).

---

## Önemli Teknik Kararlar

- **SQLite** (MVP) — `data/tefas.db`, 800+ fon × 365 gün ≈ 300K satır
- **Flow formülü:** `net_flow = (shares_t - shares_{t-1}) × price_t`
- **Fon tipleri:** YAT (Yatırım), EMK (Emeklilik), BYF (Borsa Yatırım Fonu)
- **React SPA** Flask üzerinde serve edilir (`/tefas/` prefix, tüm route'lar `index.html`'e düşer)
- **cloudscraper** — farside.co.uk Cloudflare engeli için
- **TEFAS API:** `POST https://www.tefas.gov.tr/api/DB/BindHistoryInfo`
- **AUM kaynağı:** TEFAS `portfoyBuyukluk` → `FundDaily.aum`
- **Mail:** Gmail SMTP değil **Brevo relay** (`mailer.py`, env ile değişir). SPF/DKIM/DMARC
  Cloudflare'de tanımlı; DMARC `p=reject` + `aspf=r` (Brevo dönüş adresi alt alan adından
  geldiği için `aspf=s` OLMAZ).
- **PWA:** `/sw.js` ve `/manifest.json` mutlaka `_PUBLIC_EXACT`'te olmalı — login gate'ine
  takılırsa tarayıcı HTML alır, site "kurulabilir" sayılmaz.
- **Mobil taşma:** flex/grid öğeleri varsayılan `min-width:auto` yüzünden içeriğinden
  daha dar olamaz; iOS'ta yatay taşma yapar (Chromium'da görünmez). Çözüm: `.main{min-width:0}`
  ve grid'lerde `minmax(0,1fr)`.
- **Dark mode:** `:root[data-theme="dark"]`, tercih `localStorage`'da; iframe'ler aynı
  origin olduğu için temayı paylaşır.
- **Sosyal medya kartı:** `static/og-image-v3.png` (1200×630). X kartı **sayfa URL'sine**
  göre önbelleğe alır — görselin adını değiştirmek yetmez; tazelemek için linke
  `?v=2` gibi parametre ekle.

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

# BIST endeks geçmişi (10 yıla kadar haftalık bar çeker)
python collect_bist.py

# DB init (tablo oluştur)
python -c "from tefas_backend.database import init_db; init_db()"

# .env'de bir anahtar var mı (DEĞERİ ekrana basmadan)
grep -q '^SECRET_KEY=' .env && echo VAR || echo YOK

# .env'deki değişken ADLARINI listele (değerleri gizli kalır)
cut -d= -f1 .env | grep -v '^$'
```
