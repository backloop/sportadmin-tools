// Instrumentation only — installed via page.add_init_script() so it runs in
// every frame (including the cross-origin admin.sportadmin.se attendance iframe)
// before any page script. It NEVER sends anything and NEVER clicks anything: it
// only wraps WebSocket.prototype.send / the inbound message listener and adds a
// MutationObserver, exposing window.__blazorIdle so the scraper can tell when a
// Blazor Server render batch has been applied and acked and the DOM has settled.
//
// Signal model (measured live): a UI event triggers a burst of inbound
// RenderBatch frames (>= ~50 bytes each), every one answered by a small (~20-60
// byte) outbound binary ack within a few ms; DOM mutations trail each frame by
// ~15-40 ms; then silence. 3-byte inbound frames are SignalR keepalive pings.

(function () {
  "use strict";
  if (window.__blazorIdleInstalled) return;
  window.__blazorIdleInstalled = true;

  var S = {
    frames: 0,        // inbound data frames (size > KEEPALIVE_MAX)
    acks: 0,          // outbound small binary frames (render-batch acks)
    pendingAcks: 0,   // frames - acks, floored at 0
    lastFrameTs: 0,
    lastMutTs: 0,
    installedTs: Date.now()
  };
  window.__blazorIdle = S;

  var KEEPALIVE_MAX = 20;   // SignalR ping frames are ~3 bytes
  var ACK_MIN = 6;
  var ACK_MAX = 80;

  function sizeOf(data) {
    try {
      if (data == null) return 0;
      if (typeof data === "string") return data.length;
      if (typeof data.byteLength === "number") return data.byteLength;
      if (typeof data.size === "number") return data.size;
    } catch (e) {}
    return 0;
  }

  function onInbound(data) {
    if (sizeOf(data) <= KEEPALIVE_MAX) return;
    S.frames++;
    S.pendingAcks++;
    S.lastFrameTs = Date.now();
  }

  function onOutbound(data) {
    if (typeof data === "string") return;      // SignalR handshake / negotiate JSON
    var n = sizeOf(data);
    if (n >= ACK_MIN && n <= ACK_MAX) {
      S.acks++;
      if (S.pendingAcks > 0) S.pendingAcks--;
    }
  }

  var origSend = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    try { onOutbound(data); } catch (e) {}
    return origSend.apply(this, arguments);
  };

  function wrapListener(fn) {
    if (typeof fn !== "function") return fn;
    return function (ev) {
      if (ev && ev.type === "message") {
        try { onInbound(ev.data); } catch (e) {}
      }
      return fn.apply(this, arguments);
    };
  }

  var origAdd = WebSocket.prototype.addEventListener;
  WebSocket.prototype.addEventListener = function (type, fn, opts) {
    return origAdd.call(this, type, type === "message" ? wrapListener(fn) : fn, opts);
  };

  try {
    var desc = Object.getOwnPropertyDescriptor(WebSocket.prototype, "onmessage");
    if (desc && desc.set) {
      Object.defineProperty(WebSocket.prototype, "onmessage", {
        configurable: true,
        get: desc.get,
        set: function (fn) { desc.set.call(this, wrapListener(fn)); }
      });
    }
  } catch (e) {}

  function startObserver() {
    try {
      var target = document.documentElement || document.body || document;
      if (!target) { setTimeout(startObserver, 30); return; }
      var mo = new MutationObserver(function () { S.lastMutTs = Date.now(); });
      mo.observe(target, {
        childList: true, subtree: true, characterData: true, attributes: true
      });
    } catch (e) {}
  }
  startObserver();
})();
