// Pulse Orange — 强制清除旧缓存，然后注册空 SW 占位
// 放在 <head> 最前面立即执行，确保在任何内容加载前清除缓存
(function() {
  if ('serviceWorker' in navigator) {
    // 1. 先取消所有注册
    navigator.serviceWorker.getRegistrations().then(function(regs) {
      regs.forEach(function(r) { r.unregister(); });
      // 2. 删掉所有缓存
      if (caches) {
        caches.keys().then(function(keys) {
          keys.forEach(function(k) { caches.delete(k); });
        });
      }
      // 3. 重新注册自毁 SW — 确保旧 SW 被替换
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js', { scope: '/' }).then(function(reg) {
          // 等待新 SW 激活
          if (reg.installing) {
            reg.installing.addEventListener('statechange', function() {
              if (this.state === 'activated') {
                // 新 SW 已经激活（会自毁），强制刷新页面
                window.location.reload();
              }
            });
          }
        }).catch(function() {});
      }
    });
  }
})();
