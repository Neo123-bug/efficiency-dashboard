// 全局自动刷新：每 5 分钟整页刷新一次，与后端「每 5 分钟全量刷新」节奏对齐。
// 这样局域网内任何同事打开页面，无需手动刷新即可看到最新数据。
(function () {
  var INTERVAL = 5 * 60 * 1000; // 5 分钟
  setInterval(function () {
    // 用户正在输入框里打字时不打断
    var a = document.activeElement;
    if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable)) {
      return;
    }
    location.reload();
  }, INTERVAL);
})();
