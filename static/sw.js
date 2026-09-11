const CACHE_NAME = 'sule-digital-v2';
const ASSETS = [
  '/',
  '/static/manifest.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => (key !== CACHE_NAME ? caches.delete(key) : null)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

// ---- Push notifications ----
self.addEventListener('push', (event) => {
  let data = {
    title: 'MR. SULE',
    body: 'Una taarifa mpya',
    url: '/',
    tag: 'sule-general',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png'
  };

  try {
    if (event.data) {
      const parsed = event.data.json();
      data = Object.assign(data, parsed);
    }
  } catch (err) {
    try {
      data.body = event.data ? event.data.text() : data.body;
    } catch (_) {}
  }

  const options = {
    body: data.body || 'Una taarifa mpya',
    icon: data.icon || '/static/icons/icon-192.png',
    badge: data.badge || '/static/icons/icon-192.png',
    tag: data.tag || 'sule-general',
    renotify: true,
    requireInteraction: false,
    vibrate: [120, 60, 120],
    data: {
      url: data.url || '/',
      tag: data.tag || 'sule-general'
    },
    actions: [
      { action: 'open', title: 'Fungua' },
      { action: 'dismiss', title: 'Funga' }
    ]
  };

  event.waitUntil(
    Promise.all([
      self.registration.showNotification(data.title || 'MR. SULE', options),
      self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
        clientList.forEach((client) => {
          client.postMessage({
            type: 'PUSH_NOTIFICATION',
            title: data.title || 'MR. SULE',
            body: data.body || '',
            url: data.url || '/',
            tag: data.tag || 'sule-general'
          });
        });
      })
    ])
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'dismiss') return;

  const targetUrl = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.focus();
          if (client.navigate) {
            try { client.navigate(targetUrl); } catch (_) {}
          }
          client.postMessage({ type: 'NOTIFICATION_CLICK', url: targetUrl });
          return;
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});

self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(Promise.resolve());
});
