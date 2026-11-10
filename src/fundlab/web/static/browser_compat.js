/* Shared by the workbench and self-contained chart documents. */
(function () {
  'use strict';
  if (typeof Element.prototype.replaceChildren !== 'function') {
    Object.defineProperty(Element.prototype, 'replaceChildren', {
      configurable: true, writable: true,
      value: function () {
        var nodes = [];
        for (var i = 0; i < arguments.length; i++) {
          var value = arguments[i];
          nodes.push(value instanceof Node ? value : this.ownerDocument.createTextNode(value));
        }
        var replacement = nodes.length === 1 ? nodes[0] : this.ownerDocument.createDocumentFragment();
        if (nodes.length !== 1) for (var j = 0; j < nodes.length; j++) replacement.appendChild(nodes[j]);
        // Validate before removing existing children (including hierarchy errors).
        if ([1, 3, 4, 7, 8, 11].indexOf(replacement.nodeType) === -1 || replacement.contains(this)) {
          throw new DOMException('Invalid child hierarchy', 'HierarchyRequestError');
        }
        while (this.firstChild) this.removeChild(this.firstChild);
        this.appendChild(replacement);
      }
    });
  }
  function requestId() {
    var secure = window.crypto;
    if (secure && typeof secure.randomUUID === 'function') return secure.randomUUID();
    if (!secure || typeof secure.getRandomValues !== 'function') {
      throw new Error('当前浏览器缺少安全随机功能，任务尚未提交。请使用新版 Microsoft Edge 打开 QFund。');
    }
    var bytes = secure.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    var hex = [];
    for (var i = 0; i < bytes.length; i++) hex.push(('0' + bytes[i].toString(16)).slice(-2));
    return hex.slice(0, 4).join('') + '-' + hex.slice(4, 6).join('') + '-' +
      hex.slice(6, 8).join('') + '-' + hex.slice(8, 10).join('') + '-' + hex.slice(10).join('');
  }
  window.QFundBrowserCompat = Object.freeze({requestId: requestId});
})();
