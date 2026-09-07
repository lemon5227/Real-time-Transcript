/* EchoNote live classroom workbench. No framework required: the page stays fast on thin laptops. */
(function () {
  "use strict";

  var $ = function (selector) { return document.querySelector(selector); };
  var $$ = function (selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); };
  var feed = $("#transcript-feed");
  var emptyState = $("#empty-state");
  var startButton = $("#start-listening");
  var startLabel = $("#start-listening-label");
  var state = {
    recording: false,
    connected: false,
    socket: null,
    stream: null,
    context: null,
    source: null,
    captureNode: null,
    fallbackProcessor: null,
    sequence: 0,
    segments: [],
    sessionStartedAt: null,
    timer: null,
    capabilities: null,
    mode: "auto"
  };

  function setStatus(kind, connection, model) {
    var dot = $("#status-dot");
    dot.className = "status-dot" + (kind ? " is-" + kind : "");
    $("#connection-status").textContent = connection;
    if (model) $("#model-status").textContent = model;
  }

  function formatDuration(seconds) {
    var minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
    var remainder = Math.floor(seconds % 60).toString().padStart(2, "0");
    return minutes + ":" + remainder;
  }

  function updateClock() {
    if (!state.sessionStartedAt) return;
    $("#live-clock").textContent = formatDuration((Date.now() - state.sessionStartedAt) / 1000);
  }

  function setRecordingUi(recording) {
    state.recording = recording;
    startButton.classList.toggle("is-recording", recording);
    $("#sound-bars").classList.toggle("is-active", recording);
    startLabel.textContent = recording ? "结束听课" : "开始听课";
    $("#recording-state").textContent = recording ? "正在收音与转录" : "麦克风已就绪";
    $("#recording-device").textContent = recording ? "音频仅按当前推理路径处理" : "浏览器将请求你的麦克风权限";
    if (recording) {
      setStatus("live", "正在实时转录", $("#model-status").textContent);
      state.timer = window.setInterval(updateClock, 1000);
    } else {
      window.clearInterval(state.timer);
      state.timer = null;
    }
  }

  function showError(message, detail) {
    setStatus("error", message, detail || "请检查设置后重试");
    $("#feed-hint").textContent = message;
  }

  function updateCapabilityUi(data) {
    state.capabilities = data;
    var device = data.local && data.local.device ? data.local.device : {};
    var localAvailable = Boolean(data.local && data.local.available);
    var cloudAvailable = Boolean(data.cloud && data.cloud.configured);
    var title = localAvailable ? "本地路径可用" : cloudAvailable ? "云端路径已就绪" : "需要准备一个转录路径";
    var note = localAvailable
      ? (device.label || "当前设备") + " · 推荐 " + (data.local.recommended_model || "small")
      : cloudAvailable
        ? "本地模型不可用时会使用已配置云端"
        : "安装本地依赖，或在 .env 中配置云端模型";
    $("#capability-title").textContent = title;
    $("#capability-note").textContent = note;
    $("#model-status").textContent = localAvailable ? "本地模型已检查" : cloudAvailable ? "云端模型已检查" : "等待模型配置";
    $("#capability-panel").classList.toggle("is-warning", !localAvailable && !cloudAvailable);
    var modelSelect = $("#model-select");
    if (data.local && data.local.recommended_model && modelSelect.querySelector('[value="' + data.local.recommended_model + '"]')) {
      modelSelect.value = data.local.recommended_model;
    }
  }

  function loadCapabilities() {
    return fetch("/api/capabilities")
      .then(function (response) { if (!response.ok) throw new Error("capabilities request failed"); return response.json(); })
      .then(updateCapabilityUi)
      .catch(function () {
        $("#capability-title").textContent = "设备检测暂不可用";
        $("#capability-note").textContent = "你仍可以尝试开始，或检查后端是否已启动。";
      });
  }

  function addSegment(segment) {
    if (!segment || !segment.text) return;
    state.segments.push(segment);
    emptyState.hidden = true;
    var article = document.createElement("article");
    article.className = "transcript-segment";
    var time = document.createElement("time");
    time.className = "segment-time";
    time.textContent = formatDuration(Math.max(0, Number(segment.start_ms || 0) / 1000));
    var content = document.createElement("div");
    content.className = "segment-content";
    var text = document.createElement("p");
    text.textContent = segment.text;
    var meta = document.createElement("small");
    var confidence = Number(segment.confidence);
    meta.textContent = Number.isFinite(confidence) && confidence > 0 ? "识别可信度 " + Math.round(confidence * 100) + "%" : "已确认字幕";
    content.appendChild(text);
    content.appendChild(meta);
    article.appendChild(time);
    article.appendChild(content);
    feed.appendChild(article);
    $("#segment-count").textContent = state.segments.length;
    if ($("#autoscroll-toggle").checked) feed.scrollTop = feed.scrollHeight;
  }

  function clearTranscript() {
    $$(".transcript-segment").forEach(function (node) { node.remove(); });
    state.segments = [];
    emptyState.hidden = false;
    $("#segment-count").textContent = "0";
  }

  function encodeBase64(arrayBuffer) {
    var bytes = new Uint8Array(arrayBuffer);
    var chunk = 0x8000;
    var binary = "";
    for (var i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
    }
    return window.btoa(binary);
  }

  function sendAudioBuffer(buffer) {
    if (!state.recording || !state.socket || !state.connected || !buffer || !buffer.byteLength) return;
    state.socket.emit("audio_chunk", {
      audio: encodeBase64(buffer),
      sample_rate: state.context ? state.context.sampleRate : 16000,
      sequence: state.sequence++
    });
  }

  function floatToPcm16(floatArray) {
    var pcm = new Int16Array(floatArray.length);
    for (var i = 0; i < floatArray.length; i += 1) {
      var sample = Math.max(-1, Math.min(1, floatArray[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return pcm.buffer;
  }

  async function beginCapture() {
    state.context = new (window.AudioContext || window.webkitAudioContext)();
    state.source = state.context.createMediaStreamSource(state.stream);
    if (state.context.audioWorklet && window.AudioWorkletNode) {
      await state.context.audioWorklet.addModule("/static/audio-worklet.js");
      state.captureNode = new AudioWorkletNode(state.context, "pcm-capture");
      state.captureNode.port.onmessage = function (event) { sendAudioBuffer(event.data); };
      state.source.connect(state.captureNode);
      var silentGain = state.context.createGain();
      silentGain.gain.value = 0;
      state.captureNode.connect(silentGain).connect(state.context.destination);
    } else {
      state.fallbackProcessor = state.context.createScriptProcessor(4096, 1, 1);
      state.fallbackProcessor.onaudioprocess = function (event) { sendAudioBuffer(floatToPcm16(event.inputBuffer.getChannelData(0))); };
      state.source.connect(state.fallbackProcessor);
      state.fallbackProcessor.connect(state.context.destination);
    }
    if (state.context.state === "suspended") await state.context.resume();
  }

  function releaseCapture() {
    if (state.captureNode) state.captureNode.disconnect();
    if (state.fallbackProcessor) state.fallbackProcessor.disconnect();
    if (state.source) state.source.disconnect();
    if (state.stream) state.stream.getTracks().forEach(function (track) { track.stop(); });
    if (state.context) state.context.close();
    state.captureNode = null;
    state.fallbackProcessor = null;
    state.source = null;
    state.stream = null;
    state.context = null;
  }

  function persistSession(stopResult) {
    var databaseRequest = window.indexedDB && window.indexedDB.open("echonote", 1);
    if (!databaseRequest) return;
    databaseRequest.onupgradeneeded = function () { databaseRequest.result.createObjectStore("sessions", { keyPath: "id" }); };
    databaseRequest.onsuccess = function () {
      var transaction = databaseRequest.result.transaction("sessions", "readwrite");
      transaction.objectStore("sessions").put({
        id: (stopResult && stopResult.session_id) || "session-" + Date.now(),
        title: $("#course-title").value.trim() || "未命名课堂",
        created_at: new Date().toISOString(),
        duration_seconds: state.sessionStartedAt ? Math.round((Date.now() - state.sessionStartedAt) / 1000) : 0,
        provider: stopResult && stopResult.provider,
        model: stopResult && stopResult.model,
        segments: (stopResult && stopResult.segments) || state.segments
      });
    };
  }

  function finishSession(result) {
    if (result && result.segments) {
      state.segments = [];
      $$(".transcript-segment").forEach(function (node) { node.remove(); });
      result.segments.forEach(addSegment);
    }
    persistSession(result || {});
    releaseCapture();
    setRecordingUi(false);
    setStatus("ready", "本次听课已保存", "可以前往课后复习");
  }

  async function startListening() {
    if (state.recording) return stopListening();
    if (!state.socket || !state.connected) return showError("实时连接尚未就绪", "请确认后端已启动");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return showError("当前浏览器不支持麦克风", "请使用最新版 Chrome、Edge 或 Safari");
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      var payload = {
        mode: state.mode,
        model: $("#model-select").value,
        language: $("#language-select").value,
        sample_rate: 16000,
        enable_vad: true
      };
      state.socket.emit("start_transcription", payload, async function (result) {
        if (!result || result.status !== "success") {
          releaseCapture();
          return showError(result && result.error ? result.error.message : "无法开始转录", result && result.error ? result.error.action : "请切换推理路径后重试");
        }
        state.sequence = 0;
        state.sessionStartedAt = Date.now();
        $("#model-status").textContent = (result.provider || "provider") + (result.model ? " · " + result.model : "");
        try {
          await beginCapture();
          setRecordingUi(true);
        } catch (captureError) {
          releaseCapture();
          state.socket.emit("stop_transcription", {});
          showError("麦克风启动失败", captureError.message);
        }
      });
    } catch (error) {
      releaseCapture();
      showError("没有获得麦克风权限", "请在浏览器地址栏允许麦克风后重试");
    }
  }

  function stopListening() {
    if (!state.recording) return;
    releaseCapture();
    if (state.socket && state.connected) {
      state.socket.emit("stop_transcription", {}, function (result) { finishSession(result || {}); });
    } else {
      setRecordingUi(false);
      setStatus("error", "实时连接已断开", "本次内容仍保留在页面中");
    }
  }

  function setupModes() {
    $$(".mode-option").forEach(function (button) {
      button.addEventListener("click", function () {
        state.mode = button.dataset.mode;
        $$(".mode-option").forEach(function (option) { option.classList.toggle("is-selected", option === button); option.setAttribute("aria-pressed", option === button ? "true" : "false"); });
        $("#privacy-copy").textContent = state.mode === "cloud" ? "云端模式会发送当前麦克风音频到你配置的服务；请确认服务商的隐私政策。" : state.mode === "local" ? "本地模式在你的设备上处理音频，适合重视隐私的课堂。" : "自动模式会优先尝试本地处理；云端模式只在你主动选择后发送音频。";
      });
    });
  }

  function setupSocket() {
    if (!window.io) return showError("实时连接脚本未加载", "联网加载 Socket.IO 失败，请检查网络或将客户端脚本本地化");
    state.socket = window.io({ transports: ["websocket", "polling"] });
    state.socket.on("connect", function () { state.connected = true; if (!state.recording) setStatus("ready", "实时连接已就绪", $("#model-status").textContent); });
    state.socket.on("disconnect", function () { state.connected = false; if (state.recording) showError("实时连接中断", "请停止并重新开始本次听课"); else setStatus("error", "实时连接已断开", "请检查后端服务"); });
    state.socket.on("transcript_segment", addSegment);
    state.socket.on("transcription_error", function (error) { showError(error.message || "转录出现问题", error.action); });
    state.socket.on("transcription_stopped", function (result) { if (state.recording) finishSession(result); });
  }

  function setupHelp() {
    var dialog = $("#help-dialog");
    $("#help-button").addEventListener("click", function () { if (dialog.showModal) dialog.showModal(); });
    $("#close-help").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
  }

  startButton.addEventListener("click", startListening);
  $("#clear-transcript").addEventListener("click", clearTranscript);
  setupModes();
  setupHelp();
  setupSocket();
  loadCapabilities();
  document.addEventListener("keydown", function (event) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (event.key === " " && tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA") { event.preventDefault(); startListening(); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#course-title").focus(); }
  });
})();
