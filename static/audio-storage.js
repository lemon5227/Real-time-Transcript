(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.createAudioRepository = api.createAudioRepository;
    root.EchoAudioRepository = api.createAudioRepository({ root: root, storage: root.EchoStore });
  }
}(typeof self !== "undefined" && self.window ? self.window : typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var AUDIO_ROOT = "audio";

  function error(message, code) {
    var result = new Error(message);
    result.code = code;
    return result;
  }

  function normalizeManifest(manifest) {
    var record = manifest || {};
    return {
      version: Number(record.version) || 1,
      sessionId: String(record.sessionId || ""),
      enabled: record.enabled !== false,
      status: String(record.status || "recording"),
      storage: String(record.storage || "none"),
      mimeType: String(record.mimeType || "audio/webm"),
      extension: String(record.extension || "webm"),
      chunkCount: Number(record.chunkCount) || 0,
      bytes: Number(record.bytes) || 0,
      durationMs: Number(record.durationMs) || 0,
      startedAt: record.startedAt || new Date().toISOString(),
      completedAt: record.completedAt || null,
      error: String(record.error || "")
    };
  }

  function safeDirectoryName(sessionId) {
    return encodeURIComponent(String(sessionId));
  }

  function chunkName(sequence) {
    return String(Number(sequence)).padStart(10, "0") + ".chunk";
  }

  function chunkSequence(name) {
    var match = String(name).match(/^(\d+)\.chunk$/);
    return match ? Number(match[1]) : null;
  }

  function createIndexedDbBackend(storage) {
    if (!storage || typeof storage.saveAudioManifest !== "function") throw error("本地音频存储不可用", "AUDIO_STORAGE_UNAVAILABLE");
    return {
      kind: "indexeddb",
      createManifest: function (manifest) { return storage.saveAudioManifest(manifest); },
      getManifest: function (sessionId) { return storage.getAudioManifest(sessionId); },
      putChunk: function (chunk) { return storage.saveAudioChunk(chunk); },
      listChunks: function (sessionId) { return storage.listAudioChunks(sessionId); },
      delete: function (sessionId) { return storage.deleteAudioAssets(sessionId); }
    };
  }

  function createOpfsBackend(rootDirectory, storage) {
    var audioRootPromise = rootDirectory.getDirectoryHandle(AUDIO_ROOT, { create: true });

    function sessionDirectory(sessionId, create) {
      return audioRootPromise.then(function (audioRoot) {
        return audioRoot.getDirectoryHandle(safeDirectoryName(sessionId), { create: Boolean(create) });
      });
    }

    return {
      kind: "opfs",
      createManifest: function (manifest) { return storage.saveAudioManifest(manifest); },
      getManifest: function (sessionId) { return storage.getAudioManifest(sessionId); },
      putChunk: function (chunk) {
        return sessionDirectory(chunk.sessionId, true).then(function (directory) {
          return directory.getFileHandle(chunkName(chunk.sequence), { create: true });
        }).then(function (fileHandle) {
          return fileHandle.createWritable().then(function (writable) {
            return writable.write(chunk.blob).then(function () { return writable.close(); });
          });
        });
      },
      listChunks: function (sessionId) {
        return sessionDirectory(sessionId, false).then(async function (directory) {
          var chunks = [];
          for await (var entry of directory.entries()) {
            var sequence = chunkSequence(entry[0]);
            if (sequence === null || entry[1].kind !== "file") continue;
            chunks.push({
              sessionId: sessionId,
              sequence: sequence,
              blob: await entry[1].getFile()
            });
          }
          return chunks;
        }).catch(function (caught) {
          if (caught && caught.name === "NotFoundError") return [];
          throw caught;
        });
      },
      delete: function (sessionId) {
        return audioRootPromise.then(function (audioRoot) {
          return audioRoot.removeEntry(safeDirectoryName(sessionId), { recursive: true }).catch(function (caught) {
            if (!caught || caught.name !== "NotFoundError") throw caught;
          });
        }).then(function () { return storage.deleteAudioAssets(sessionId); });
      }
    };
  }

  function selectBackend(config) {
    if (config.backend) return Promise.resolve(config.backend);
    var root = config.root || {};
    var storage = config.storage || root.EchoStore;
    var navigator = root.navigator;
    if (navigator && navigator.storage && typeof navigator.storage.getDirectory === "function") {
      return Promise.resolve().then(function () { return navigator.storage.getDirectory(); }).then(function (directory) {
        return createOpfsBackend(directory, storage);
      }).catch(function () { return createIndexedDbBackend(storage); });
    }
    return Promise.resolve(createIndexedDbBackend(storage));
  }

  function createAudioRepository(options) {
    var config = options || {};
    var root = config.root || (typeof window !== "undefined" ? window : globalThis);
    var backendPromise = config.backend ? Promise.resolve(config.backend) : null;

    function backend() {
      if (!backendPromise) backendPromise = selectBackend({ root: root, storage: config.storage || root.EchoStore });
      return backendPromise;
    }

    function createRecording(options) {
      var record = options || {};
      if (!record.sessionId) return Promise.reject(error("缺少课堂编号", "AUDIO_SESSION_REQUIRED"));
      return backend().then(function (store) {
        return store.getManifest(record.sessionId).then(function (existing) {
          if (existing) return normalizeManifest(existing);
          return store.createManifest(normalizeManifest({
            sessionId: record.sessionId,
            enabled: true,
            status: "recording",
            storage: store.kind || "indexeddb",
            mimeType: record.mimeType,
            extension: record.extension,
            startedAt: record.startedAt
          }));
        });
      });
    }

    function appendChunk(options) {
      var record = options || {};
      if (!record.sessionId || Number(record.sequence) < 0 || !record.blob || !record.blob.size) return Promise.resolve();
      return backend().then(function (store) {
        return store.getManifest(record.sessionId).then(function (manifest) {
          if (!manifest) throw error("课堂录音尚未初始化", "AUDIO_SESSION_REQUIRED");
          return store.listChunks(record.sessionId).then(function (chunks) {
            if (chunks.some(function (chunk) { return Number(chunk.sequence) === Number(record.sequence); })) return;
            return store.putChunk({
              sessionId: record.sessionId,
              sequence: Number(record.sequence),
              blob: record.blob,
              startMs: Math.max(0, Number(record.startMs) || 0),
              endMs: Math.max(0, Number(record.endMs) || 0),
              bytes: Number(record.blob.size) || 0
            }).then(function () {
              var updated = normalizeManifest(manifest);
              updated.chunkCount = chunks.length + 1;
              updated.bytes = Math.max(0, Number(updated.bytes) || 0) + Number(record.blob.size || 0);
              return store.createManifest(updated);
            });
          });
        });
      });
    }

    function finalizeRecording(options) {
      var record = options || {};
      return backend().then(function (store) {
        return store.getManifest(record.sessionId).then(function (manifest) {
          if (!manifest) throw error("课堂录音不存在", "AUDIO_SESSION_REQUIRED");
          var updated = normalizeManifest(manifest);
          updated.status = "ready";
          updated.durationMs = Math.max(0, Number(record.durationMs) || updated.durationMs);
          updated.completedAt = new Date().toISOString();
          updated.error = "";
          return store.createManifest(updated);
        });
      });
    }

    function getManifest(sessionId) { return backend().then(function (store) { return store.getManifest(sessionId).then(function (manifest) { return manifest ? normalizeManifest(manifest) : null; }); }); }

    function getPlayableBlob(sessionId) {
      return backend().then(function (store) {
        return store.getManifest(sessionId).then(function (manifest) {
          if (!manifest) return null;
          return store.listChunks(sessionId).then(function (chunks) {
            if (!chunks.length) return null;
            chunks.sort(function (left, right) { return Number(left.sequence) - Number(right.sequence); });
            return new Blob(chunks.map(function (chunk) { return chunk.blob; }), { type: manifest.mimeType });
          });
        });
      });
    }

    function deleteRecording(sessionId) { return backend().then(function (store) { return store.delete(sessionId); }); }

    function getUsage() {
      var navigator = root && root.navigator;
      if (!navigator || !navigator.storage || typeof navigator.storage.estimate !== "function") return Promise.resolve(null);
      return navigator.storage.estimate().then(function (estimate) {
        return { usage: Number(estimate.usage) || 0, quota: Number(estimate.quota) || 0 };
      }).catch(function () { return null; });
    }

    return {
      createRecording: createRecording,
      appendChunk: appendChunk,
      finalizeRecording: finalizeRecording,
      getManifest: getManifest,
      getPlayableBlob: getPlayableBlob,
      deleteRecording: deleteRecording,
      getUsage: getUsage
    };
  }

  return { createAudioRepository: createAudioRepository };
}));
