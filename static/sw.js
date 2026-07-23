/* 3N Finans — Service Worker
 *
 * TASARIM İLKESİ: finansal veri asla önbellekten sunulmaz.
 * - /api/*            → HER ZAMAN ağdan. Bayat fon fiyatı/akış verisi göstermek
 *                       kullanıcıyı yanıltır; bu yüzden hiç cache'lenmez.
 * - Statik varlıklar  → cache-first (dosya adları hash'li olduğu için güvenli)
 * - Sayfa gezinmeleri → network-first, çevrimdışıysa son görülen sayfa
 *
 * Sürümü değiştirmek eski cache'i temizler.
 */
const VERSION = 'v3';
const STATIC_CACHE = `3nf-static-${VERSION}`;
const PAGE_CACHE = `3nf-pages-${VERSION}`;

// Kurulumda önden alınacak minimum kabuk
const PRECACHE = [
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      // tek dosya hata verirse kurulum çökmesin
      .then((c) => Promise.allSettled(PRECACHE.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== STATIC_CACHE && k !== PAGE_CACHE)
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

/* ── Web Push ─────────────────────────────────────────────────────────────
 * Sunucu push.py üzerinden {title, body, url, tag, icon} JSON'u yollar. */
self.addEventListener('push', (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = {}; }
  const title = d.title || '3N Finans';
  event.waitUntil(
    self.registration.showNotification(title, {
      body: d.body || '',
      icon: d.icon || '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      tag: d.tag || 'genel',
      data: { url: d.url || '/' },
      renotify: true,
    })
  );
});

/* Bildirime tıklayınca: açık sekme varsa ona odaklan, yoksa yeni aç */
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (c.url.indexOf(self.location.origin) === 0 && 'focus' in c) {
          c.navigate(target);
          return c.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Sadece kendi origin'imiz + GET
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;

  // Finansal veri ve oturum uçları: asla cache'leme
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/admin/') ||
      url.pathname.startsWith('/auth/') ||
      url.pathname.startsWith('/e/o/') ||
      url.pathname === '/login' || url.pathname === '/logout') {
    return; // tarayıcının normal ağ davranışına bırak
  }

  // Değişmez varlıklar (ikonlar, hash'li build dosyaları): cache-first güvenli
  if (url.pathname.startsWith('/static/icons/') || url.pathname.startsWith('/tefas/assets/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }))
    );
    return;
  }

  // CSS/JS SÜRÜMSÜZ (style.css, main.js) → cache-first olursa kullanıcı eski
  // arayüzde kilitli kalır. Bu yüzden ağ önce, çevrimdışıysa cache.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Sayfa gezinmeleri: önce ağ, çevrimdışıysa son kopya
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(PAGE_CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match('/')))
    );
  }
});
