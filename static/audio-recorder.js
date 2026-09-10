(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.EchoAudioRecorder = api;
}(typeof self !== "undefined" && self.window ? self.window : typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  var TIMESLICE_MS = 10000;
  var MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];

  function createError(message, code) {
    var result = new Error(message);
    result.code = code;
    return result;
  }

  function supportedMimeType(RecorderClass) {
    if (!RecorderClass) throw createError("当前浏览器不支持原声录音", "MEDIA_RECORDER_UNSUPPORTED");
    if (typeof RecorderClass.isTypeSupported !== "function") return MIME_TYPES[0];
    for (var index = 0; index < MIME_TYPES.length; index += 1) {
      if (RecorderClass.isTypeSupported(MIME_TYPES[index])) return MIME_TYPES[index];
    }
    throw createError("当前浏览器不支持可用的音频格式", "MEDIA_RECORDER_UNSUPPORTED");
  }

  function extensionForMime(mimeType) {
    if (/mp4/i.test(mimeType)) return "mp4";
    if (/ogg/i.test(mimeType)) return "ogg";
    return "webm";
  }

  function clockFunction(now) {
    if (typeof now === "function") return now;
    if (root.performance && typeof root.performance.now === "function") return root.performance.now.bind(root.performance);
    return Date.now;
  }

  function create(options) {
    var config = options || {};
    var RecorderClass = config.MediaRecorder || root.MediaRecorder;
    var repository = config.repository;
    var sessionId = config.sessionId;
    var now = clockFunction(config.now);
    var state = "idle";
    var recorder = null;
    var manifest = null;
    var sequence = 0;
    var startedAtMs = 0;
    var lastEndMs = 0;
    var writeQueue = Promise.resolve();
    var stopPromise = null;

    if (!repository || typeof repository.createRecording !== "function" || typeof repository.appendChunk !== "function" || typeof repository.finalizeRecording !== "function") {
      throw createError("原声存储接口不可用", "AUDIO_REPOSITORY_UNAVAILABLE");
    }

    function enqueueChunk(blob) {
      if (!blob || !blob.size) return;
      var endMs = Math.max(lastEndMs, Math.round(now() - startedAtMs));
      var chunk = {
        sessionId: sessionId,
        sequence: sequence,
        blob: blob,
        startMs: lastEndMs,
        endMs: endMs
      };
      sequence += 1;
      lastEndMs = endMs;
      writeQueue = writeQueue.then(function () { return repository.appendChunk(chunk); });
    }

    function removeListeners() {
      if (!recorder) return;
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
      recorder = null;
    }

    function failedRecording(caught) {
      var failure = caught instanceof Error ? caught : createError(String(caught || "原声保存失败"), "AUDIO_SAVE_FAILED");
      state = "failed";
      removeListeners();
      return repository.finalizeRecording({ sessionId: sessionId, durationMs: lastEndMs, status: "partial", error: failure.message }).catch(function () { return null; }).then(function () {
        throw failure;
      });
    }

    function start() {
      if (state === "recording") return Promise.resolve(manifest);
      if (state !== "idle") return Promise.reject(createError("原声录音已经结束", "AUDIO_RECORDER_FINISHED"));
      if (!sessionId) return Promise.reject(createError("缺少课堂编号", "AUDIO_SESSION_REQUIRED"));
      var mimeType;
      try { mimeType = supportedMimeType(RecorderClass); } catch (caught) { state = "failed"; return Promise.reject(caught); }
      try { recorder = new RecorderClass(config.stream, { mimeType: mimeType }); } catch (caught) { state = "failed"; return Promise.reject(caught); }
      recorder.ondataavailable = function (event) { enqueueChunk(event && event.data); };
      recorder.onerror = function (event) { writeQueue = writeQueue.catch(function () {}).then(function () { throw event && event.error ? event.error : createError("原声录音发生错误", "AUDIO_RECORDING_FAILED"); }); };
      startedAtMs = Number(config.startedAtMs) > 0 ? Number(config.startedAtMs) : now();
      lastEndMs = 0;
      sequence = 0;
      return repository.createRecording({ sessionId: sessionId, mimeType: mimeType, extension: extensionForMime(mimeType), startedAt: new Date().toISOString() }).then(function (created) {
        manifest = created;
        recorder.start(TIMESLICE_MS);
        state = "recording";
        return manifest;
      }).catch(function (caught) {
        state = "failed";
        removeListeners();
        throw caught;
      });
    }

    function stop() {
      if (state === "stopped") return Promise.resolve(manifest);
      if (state === "stopping") return stopPromise;
      if (state !== "recording" || !recorder) return Promise.reject(createError("原声录音尚未开始", "AUDIO_RECORDER_NOT_RECORDING"));
      state = "stopping";
      stopPromise = new Promise(function (resolve, reject) {
        recorder.onstop = function () {
          writeQueue.then(function () {
            return repository.finalizeRecording({ sessionId: sessionId, durationMs: lastEndMs });
          }).then(function (finished) {
            manifest = finished;
            state = "stopped";
            removeListeners();
            resolve(manifest);
          }).catch(function (caught) {
            failedRecording(caught).then(function () {}, function (failure) { reject(failure); });
          });
        };
        try { recorder.stop(); } catch (caught) { failedRecording(caught).then(function () {}, function (failure) { reject(failure); }); }
      });
      return stopPromise;
    }

    return { start: start, stop: stop, getState: function () { return state; } };
  }

  return { create: create, supportedMimeType: supportedMimeType, extensionForMime: extensionForMime };
}));
