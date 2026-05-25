const CACHE = 'quant-pulse-v3';
const ASSETS = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.json',
  'https://cdn.bootcdn.net/ajax/libs/vue/3.4.21/vue.global.prod.min.js',
];

// Install: pre-cache static assets (NOT the root page)
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS).catch(_ => {}))
  );
  self.skipWaiting();
});

// Activate: clean old caches, claim all clients
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// Fetch: network-first for HTML, cache-first for static, stale-while-revalidate for API
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  const dest = e.request.destination;

  // HTML pages: always network-first
  if (dest === 'document') {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const clone = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // API: stale-while-revalidate
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const clone = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Static assets: cache-first
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
