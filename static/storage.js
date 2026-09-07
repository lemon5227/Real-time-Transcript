(function (root) {
  "use strict";

  var DATABASE = "realtime-transcript";
  var STORE = "sessions";
  var VERSION = 1;

  function openSessionStore() {
    return new Promise(function (resolve, reject) {
      if (!root.indexedDB) return reject(new Error("当前浏览器不支持本地保存"));
      var request;
      try { request = root.indexedDB.open(DATABASE, VERSION); } catch (error) { reject(error); return; }
      request.onupgradeneeded = function () {
        if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: "id" });
      };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error || new Error("无法打开本地课堂笔记")); };
    });
  }

  function transactionRequest(mode, action) {
    return openSessionStore().then(function (database) {
      return new Promise(function (resolve, reject) {
        var transaction;
        try {
          transaction = database.transaction(STORE, mode);
          var request = action(transaction.objectStore(STORE));
          request.onsuccess = function () { resolve(request.result); };
          request.onerror = function () { reject(request.error || new Error("本地课堂笔记操作失败")); };
          transaction.onabort = function () { reject(transaction.error || new Error("本地课堂笔记操作被取消")); };
        } catch (error) { reject(error); }
      }).finally(function () { database.close(); });
    });
  }

  function normalizeSegment(segment, index) {
    return {
      id: segment.id || "segment-" + index,
      text: String(segment.text || "").trim(),
      startMs: Number(segment.startMs !== undefined ? segment.startMs : segment.start_ms) || 0,
      endMs: Number(segment.endMs !== undefined ? segment.endMs : segment.end_ms) || 0,
      note: String(segment.note || ""),
      starred: Boolean(segment.starred),
      isFinal: segment.isFinal !== undefined ? Boolean(segment.isFinal) : segment.is_final !== false
    };
  }

  function normalizeSession(session) {
    var record = session || {};
    return {
      id: record.id || "session-" + Date.now(),
      title: String(record.title || "未命名课堂"),
      createdAt: record.createdAt || record.created_at || new Date().toISOString(),
      durationMs: Number(record.durationMs !== undefined ? record.durationMs : (Number(record.duration_seconds) || 0) * 1000) || 0,
      language: String(record.language || "en"),
      provider: String(record.provider || "unknown"),
      model: String(record.model || ""),
      segments: (record.segments || []).map(normalizeSegment)
    };
  }

  function saveSession(session) { return transactionRequest("readwrite", function (store) { return store.put(normalizeSession(session)); }); }
  function listSessions() { return transactionRequest("readonly", function (store) { return store.getAll(); }).then(function (sessions) { return (sessions || []).sort(function (a, b) { return String(b.createdAt).localeCompare(String(a.createdAt)); }); }); }
  function getSession(id) { return transactionRequest("readonly", function (store) { return store.get(id); }); }
  function deleteSession(id) { return transactionRequest("readwrite", function (store) { return store.delete(id); }); }

  root.EchoStore = { openSessionStore: openSessionStore, saveSession: saveSession, listSessions: listSessions, getSession: getSession, deleteSession: deleteSession, normalizeSession: normalizeSession };
  root.openSessionStore = openSessionStore;
  root.saveSession = saveSession;
  root.listSessions = listSessions;
  root.getSession = getSession;
  root.deleteSession = deleteSession;
}(window));
