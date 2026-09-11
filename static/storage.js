(function (root) {
  "use strict";

  var DATABASE = "realtime-transcript";
  var STORE = "sessions";
  var AUDIO_MANIFEST_STORE = "audioManifests";
  var AUDIO_CHUNK_STORE = "audioChunks";
  var VERSION = 2;

  function openSessionStore() {
    return new Promise(function (resolve, reject) {
      if (!root.indexedDB) return reject(new Error("当前浏览器不支持本地保存"));
      var request;
      try { request = root.indexedDB.open(DATABASE, VERSION); } catch (error) { reject(error); return; }
      request.onupgradeneeded = function () {
        var database = request.result;
        if (!database.objectStoreNames.contains(STORE)) database.createObjectStore(STORE, { keyPath: "id" });
        if (!database.objectStoreNames.contains(AUDIO_MANIFEST_STORE)) database.createObjectStore(AUDIO_MANIFEST_STORE, { keyPath: "sessionId" });
        if (!database.objectStoreNames.contains(AUDIO_CHUNK_STORE)) database.createObjectStore(AUDIO_CHUNK_STORE, { keyPath: ["sessionId", "sequence"] });
      };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error || new Error("无法打开本地课堂笔记")); };
    });
  }

  function transactionRequest(storeNames, mode, action) {
    return openSessionStore().then(function (database) {
      return new Promise(function (resolve, reject) {
        var transaction;
        var request = null;
        var actionResult;
        try {
          transaction = database.transaction(storeNames, mode);
          actionResult = action(transaction);
          if (actionResult && typeof actionResult === "object" && "onsuccess" in actionResult) request = actionResult;
          transaction.oncomplete = function () { resolve(request ? request.result : actionResult); };
          transaction.onerror = function () { reject(transaction.error || new Error("本地课堂笔记操作失败")); };
          transaction.onabort = function () { reject(transaction.error || new Error("本地课堂笔记操作被取消")); };
        } catch (error) { reject(error); }
      }).finally(function () { database.close(); });
    });
  }

  function normalizeSegment(segment, index) {
    var translations = {};
    Object.keys(segment && segment.translations || {}).forEach(function (language) {
      var translation = segment.translations[language] || {};
      translations[String(language)] = {
        text: String(translation.text || "").trim(),
        status: ["ready", "pending", "failed"].indexOf(translation.status) !== -1 ? translation.status : "ready",
        mode: String(translation.mode || "fast"),
        provider: String(translation.provider || ""),
        model: translation.model ? String(translation.model) : "",
        updatedAt: translation.updatedAt || null,
        error: String(translation.error || "")
      };
    });
    return {
      id: segment.id || "segment-" + index,
      text: String(segment.text || "").trim(),
      startMs: Number(segment.startMs !== undefined ? segment.startMs : segment.start_ms) || 0,
      endMs: Number(segment.endMs !== undefined ? segment.endMs : segment.end_ms) || 0,
      note: String(segment.note || ""),
      starred: Boolean(segment.starred),
      isFinal: segment.isFinal !== undefined ? Boolean(segment.isFinal) : segment.is_final !== false,
      translations: translations
    };
  }

  function normalizeAudio(audio) {
    if (!audio) return null;
    return {
      version: Number(audio.version) || 1,
      enabled: audio.enabled !== false,
      status: String(audio.status || "unavailable"),
      storage: String(audio.storage || "none"),
      mimeType: String(audio.mimeType || ""),
      extension: String(audio.extension || ""),
      chunkCount: Number(audio.chunkCount) || 0,
      bytes: Number(audio.bytes) || 0,
      durationMs: Number(audio.durationMs) || 0,
      startedAt: audio.startedAt || null,
      completedAt: audio.completedAt || null,
      error: String(audio.error || "")
    };
  }

  function normalizeRefinement(refinement) {
    var record = refinement || {};
    var statuses = ["not_started", "queued", "processing", "ready", "failed"];
    var status = statuses.indexOf(String(record.status || "not_started")) !== -1 ? String(record.status || "not_started") : "not_started";
    return {
      status: status,
      provider: String(record.provider || ""),
      model: String(record.model || ""),
      progress: Math.max(0, Math.min(100, Number(record.progress) || 0)),
      startedAt: record.startedAt || record.started_at || null,
      completedAt: record.completedAt || record.completed_at || null,
      error: String(record.error || "")
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
      segments: (record.segments || []).map(normalizeSegment),
      refinedSegments: (record.refinedSegments || record.refined_segments || []).map(normalizeSegment),
      refinement: normalizeRefinement(record.refinement),
      audio: normalizeAudio(record.audio)
    };
  }

  function saveSession(session) { return transactionRequest(STORE, "readwrite", function (transaction) { return transaction.objectStore(STORE).put(normalizeSession(session)); }); }
  function listSessions() { return transactionRequest(STORE, "readonly", function (transaction) { return transaction.objectStore(STORE).getAll(); }).then(function (sessions) { return (sessions || []).sort(function (a, b) { return String(b.createdAt).localeCompare(String(a.createdAt)); }); }); }
  function getSession(id) { return transactionRequest(STORE, "readonly", function (transaction) { return transaction.objectStore(STORE).get(id); }); }
  function deleteSession(id) { return transactionRequest(STORE, "readwrite", function (transaction) { return transaction.objectStore(STORE).delete(id); }); }
  function saveAudioManifest(manifest) { return transactionRequest(AUDIO_MANIFEST_STORE, "readwrite", function (transaction) { return transaction.objectStore(AUDIO_MANIFEST_STORE).put(manifest); }); }
  function getAudioManifest(sessionId) { return transactionRequest(AUDIO_MANIFEST_STORE, "readonly", function (transaction) { return transaction.objectStore(AUDIO_MANIFEST_STORE).get(sessionId); }); }
  function saveAudioChunk(chunk) { return transactionRequest(AUDIO_CHUNK_STORE, "readwrite", function (transaction) { return transaction.objectStore(AUDIO_CHUNK_STORE).put(chunk); }); }
  function listAudioChunks(sessionId) { return transactionRequest(AUDIO_CHUNK_STORE, "readonly", function (transaction) { return transaction.objectStore(AUDIO_CHUNK_STORE).getAll(); }).then(function (chunks) { return (chunks || []).filter(function (chunk) { return chunk.sessionId === sessionId; }).sort(function (a, b) { return Number(a.sequence) - Number(b.sequence); }); }); }
  function deleteAudioAssets(sessionId) {
    return transactionRequest([AUDIO_MANIFEST_STORE, AUDIO_CHUNK_STORE], "readwrite", function (transaction) {
      var chunks = transaction.objectStore(AUDIO_CHUNK_STORE);
      var request = chunks.getAll();
      request.onsuccess = function () {
        (request.result || []).forEach(function (chunk) {
          if (chunk.sessionId === sessionId) chunks.delete([chunk.sessionId, chunk.sequence]);
        });
        transaction.objectStore(AUDIO_MANIFEST_STORE).delete(sessionId);
      };
      return request;
    });
  }

  root.EchoStore = { openSessionStore: openSessionStore, saveSession: saveSession, listSessions: listSessions, getSession: getSession, deleteSession: deleteSession, normalizeSession: normalizeSession, saveAudioManifest: saveAudioManifest, getAudioManifest: getAudioManifest, saveAudioChunk: saveAudioChunk, listAudioChunks: listAudioChunks, deleteAudioAssets: deleteAudioAssets };
  root.openSessionStore = openSessionStore;
  root.saveSession = saveSession;
  root.listSessions = listSessions;
  root.getSession = getSession;
  root.deleteSession = deleteSession;
}(window));
