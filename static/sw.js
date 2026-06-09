// Pulse Orange — 自毁式 Service Worker
// 替代旧版 SW，自动注销自身 + 清缓存 + 强制刷新页面
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // 清空所有缓存
    const keys = await caches.keys();
    await Promise.all(keys.map(k => caches.delete(k)));
    // 获取所有客户端，强制刷新
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach(c => c.navigate(c.url));
    // 注销自己
    await self.registration.unregister();
    console.log('Pulse Orange SW: 旧缓存已清除，已注销');
  })());
});

// 不拦截任何请求
self.addEventListener('fetch', () => {});
