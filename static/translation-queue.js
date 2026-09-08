(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.EchoTranslationQueue = factory();
}(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function isFinal(segment) {
    return segment && (segment.isFinal !== undefined ? segment.isFinal : segment.is_final !== false);
  }

  function createTranslationQueue(options) {
    var settings = options || {};
    var batchSize = Math.max(1, Number(settings.batchSize) || 5);
    var maxChars = Math.max(1, Number(settings.maxChars) || 3000);
    var send = typeof settings.send === "function" ? settings.send : function () { return Promise.resolve({ status: "disabled", translations: [] }); };
    var pending = [];
    var byId = new Map();
    var inFlight = null;
    var stopped = false;
    var scheduled = null;
    var lastError = null;

    function scheduleFlush() {
      if (scheduled || stopped || !pending.length) return;
      scheduled = setTimeout(function () { scheduled = null; flush(); }, 350);
    }

    function enqueue(segment) {
      if (!isFinal(segment)) return false;
      var id = String(segment.id || segment.segment_id || "");
      var text = String(segment.text || "").trim();
      if (!id || !text || stopped) return false;
      var item = { id: id, text: text };
      if (byId.has(id)) {
        var existing = byId.get(id);
        existing.text = text;
        return false;
      }
      byId.set(id, item);
      pending.push(item);
      scheduleFlush();
      return true;
    }

    function takeBatch() {
      var result = [];
      var chars = 0;
      while (pending.length && result.length < batchSize) {
        var candidate = pending[0];
        if (result.length && chars + candidate.text.length > maxChars) break;
        pending.shift();
        result.push(candidate);
        chars += candidate.text.length;
      }
      return result;
    }

    function flush() {
      if (inFlight) return inFlight;
      if (!pending.length) return Promise.resolve();
      var batch = takeBatch();
      if (!batch.length) return Promise.resolve();
      inFlight = Promise.resolve().then(function () {
        return send(batch);
      }).then(function (result) {
        lastError = null;
        if (typeof settings.onResult === "function") settings.onResult(result, batch);
        return result;
      }).catch(function (error) {
        lastError = error;
        if (typeof settings.onError === "function") settings.onError(error, batch);
        return { status: "error", error: error };
      }).then(function (result) {
        batch.forEach(function (item) { byId.delete(item.id); });
        inFlight = null;
        if (pending.length) return flush().then(function () { return result; });
        return result;
      });
      return inFlight;
    }

    function stop() {
      stopped = true;
      if (scheduled) clearTimeout(scheduled);
      scheduled = null;
      return flush();
    }

    function start() {
      stopped = false;
      lastError = null;
      return api;
    }

    function retry(items) {
      if (Array.isArray(items)) items.forEach(function (item) { enqueue({ id: item.id || item.segment_id, text: item.text, is_final: true }); });
      return flush();
    }

    var api = {
      enqueue: enqueue,
      flush: flush,
      stop: stop,
      start: start,
      retry: retry,
      getState: function () { return { pending: pending.length, inFlight: Boolean(inFlight), stopped: stopped, error: lastError }; }
    };
    return api;
  }

  return { create: createTranslationQueue, createTranslationQueue: createTranslationQueue };
}));
