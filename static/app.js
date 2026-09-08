/* EchoNote live classroom workbench. No framework required: the page stays fast on thin laptops. */
(function () {
  "use strict";

  var $ = function (selector) { return document.querySelector(selector); };
  var $$ = function (selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); };
  var feed = $("#subtitleStream");
  var emptyState = $("#empty-state");
  var currentSentence = $("#current-sentence");
  var currentText = $("#current-text");
  var returnLatest = $("#return-latest");
  var startButton = $("#start-listening");
  var startLabel = $("#start-listening-label");
  var modelSelect = $("#model-select");
  var microphoneSelect = $("#microphone-select");
  var SETTINGS_KEY = "echonote-preferences-v1";
  var state = {
    recording: false,
    starting: false,
    connected: false,
    socket: null,
    stream: null,
    context: null,
    source: null,
    captureNode: null,
    fallbackProcessor: null,
    levelAnalyser: null,
    levelData: null,
    levelRaf: null,
    audioBuffer: new window.EchoAudioBuffer.PcmChunkBuffer(0.5),
    sequence: 0,
    segments: [],
    sessionStartedAt: null,
    timer: null,
    capabilities: null,
    models: [],
    modelPollTimer: null,
    mode: "auto",
    sessionId: null,
    provider: null,
    model: null,
    language: "en",
    selectedDeviceId: "",
    saveAudio: true,
    audioReadiness: "checking",
    audioRecorder: null,
    audioManifest: null,
    audioStopPromise: null,
    stopResult: null,
    finalizing: false,
    micTestStream: null,
    micTestContext: null,
    micTestSource: null,
    micTestAnalyser: null,
    micTestRaf: null,
    micTestPassed: false,
    saveTimer: null,
    startTimer: null,
    finishing: false,
    phase: "checking"
  };

  function setStatus(kind, connection, model) {
    var dot = $("#status-dot");
    dot.className = "status-dot" + (kind ? " is-" + kind : "");
    $("#connection-status").textContent = connection;
    if (model) $("#model-status").textContent = model;
  }

  function setAppPhase(phase, message, detail) {
    state.phase = phase;
    var busy = phase === "checking" || phase === "preparing" || phase === "stopping";
    var recording = phase !== "stopping" && (phase === "recording" || state.recording);
    startButton.disabled = busy;
    startButton.classList.toggle("is-busy", busy);
    startButton.setAttribute("aria-busy", busy ? "true" : "false");
    $("#readiness-panel").setAttribute("aria-busy", phase === "checking" ? "true" : "false");
    if (phase === "recording") {
      startLabel.textContent = "结束听课";
      setStatus("live", message || "正在实时转录", detail || $("#model-status").textContent);
    } else if (phase === "preparing") {
      startLabel.textContent = message || "准备中…";
      setStatus("busy", detail || message || "正在准备听课", $("#model-status").textContent);
    } else if (phase === "stopping") {
      startLabel.textContent = "正在保存…";
      setStatus("busy", message || "正在保存本次听课", detail || "请稍候");
    } else if (phase === "saved") {
      startLabel.textContent = "开始听课";
      setStatus("ready", message || "本次听课已保存", detail || "可以前往课后复习");
    } else if (phase === "error") {
      startLabel.textContent = recording ? "结束听课" : "重新开始";
      setStatus("error", message || "出现了一点问题", detail || "请检查设置后重试");
    } else if (phase === "idle") {
      startLabel.textContent = "开始听课";
      setStatus(state.connected ? "ready" : "error", message || (state.connected ? "实时连接已就绪" : "等待实时连接"), detail || $("#model-status").textContent);
    } else if (phase === "checking") {
      startLabel.textContent = "检查中…";
      setStatus("busy", message || "正在检查听课条件", detail || "马上告诉你是否可以开始");
    }
    $("#recording-state").textContent = recording ? "正在收音与转录" : phase === "preparing" ? message || "正在准备" : phase === "stopping" ? "正在保存课堂" : phase === "error" ? "需要处理后再试" : "麦克风已就绪";
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
    if (recording) {
      setAppPhase("recording", "正在实时转录", $("#model-status").textContent);
      $("#recording-device").textContent = state.saveAudio ? "原声仅保存在本机" : "仅保存字幕";
      $("#audio-storage-status").textContent = state.saveAudio ? "正在保存原声 · 每 10 秒写入本机" : "当前只保存字幕";
      state.timer = window.setInterval(updateClock, 1000);
    } else {
      window.clearInterval(state.timer);
      state.timer = null;
      $("#recording-device").textContent = "选择设备后可先测试声音";
      if (state.phase !== "saved" && state.phase !== "error") setAppPhase("idle", "麦克风已就绪", $("#model-status").textContent);
    }
  }

  function hideError() {
    $("#actionable-error").hidden = true;
    $("#error-action").hidden = true;
  }

  function inferErrorAction(message, code) {
    var value = String(code || "") + " " + String(message || "");
    if (/MODEL|模型|依赖/.test(value)) return "download-model";
    if (/麦克风|权限|MIC/.test(value)) return "retry-mic";
    if (/云端|CLOUD|provider/i.test(value)) return "switch-cloud";
    return "";
  }

  function showError(message, detail, action, code) {
    var actionKey = action || inferErrorAction(message, code);
    setAppPhase("error", message, detail || "请检查设置后重试");
    $("#feed-hint").textContent = message;
    $("#error-title").textContent = message;
    $("#error-detail").textContent = detail || "请检查设置后重试";
    var actionButton = $("#error-action");
    actionButton.dataset.action = actionKey;
    actionButton.textContent = actionKey === "download-model" ? "下载模型" : actionKey === "retry-mic" ? "重试麦克风" : actionKey === "switch-cloud" ? "切换到云端" : actionKey === "disable-audio" ? "关闭原声保存" : "重试";
    actionButton.hidden = !actionKey;
    $("#actionable-error").hidden = !actionKey;
    renderReadiness();
  }

  function getSavedPreferences() {
    try { return JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || "{}"); } catch (_error) { return {}; }
  }

  function savePreferences() {
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        mode: state.mode,
        model: modelSelect.value,
        language: $("#language-select").value,
        courseTitle: $("#course-title").value.trim(),
        deviceId: state.selectedDeviceId,
        saveAudio: state.saveAudio
      }));
    } catch (_error) { /* localStorage is optional */ }
  }

  function restorePreferences() {
    var saved = getSavedPreferences();
    if (["auto", "local", "cloud"].indexOf(saved.mode) !== -1) state.mode = saved.mode;
    if (saved.model && modelSelect.querySelector('[value="' + saved.model + '"]')) modelSelect.value = saved.model;
    if (saved.language && $("#language-select").querySelector('[value="' + saved.language + '"]')) $("#language-select").value = saved.language;
    if (saved.courseTitle) $("#course-title").value = saved.courseTitle;
    state.saveAudio = saved.saveAudio !== false;
    $("#save-audio").checked = state.saveAudio;
    state.selectedDeviceId = saved.deviceId || "";
    $$(".mode-option").forEach(function (option) {
      var selected = option.dataset.mode === state.mode;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  function selectedModel() {
    return state.models.find(function (model) { return model.id === modelSelect.value; }) || null;
  }

  function updateCapabilityUi(data) {
    state.capabilities = data;
    var device = data.local && data.local.device ? data.local.device : {};
    var localAvailable = Boolean(data.local && data.local.available);
    var cloudAvailable = Boolean(data.cloud && data.cloud.configured);
    var readyCount = data.local && Array.isArray(data.local.ready_models) ? data.local.ready_models.length : 0;
    var title = localAvailable ? "本地路径可用" : cloudAvailable ? "云端路径已就绪" : "需要准备一个转录路径";
    var note = localAvailable
      ? (device.label || "当前设备") + " · 已准备 " + readyCount + " 个本地模型 · 推荐 " + (data.local.recommended_model || "small")
      : cloudAvailable
        ? "本地模型不可用时可以使用已配置云端"
        : "安装本地依赖，或在 .env 中配置云端模型";
    $("#capability-title").textContent = title;
    $("#capability-note").textContent = note;
    $("#model-status").textContent = localAvailable ? "本地能力已检查" : cloudAvailable ? "云端能力已检查" : "等待模型配置";
    $("#capability-panel").classList.toggle("is-warning", !localAvailable && !cloudAvailable);
    renderReadiness();
  }

  function loadCapabilities() {
    return fetch("/api/capabilities")
      .then(function (response) { if (!response.ok) throw new Error("capabilities request failed"); return response.json(); })
      .then(updateCapabilityUi)
      .catch(function () {
        $("#capability-title").textContent = "设备检测暂不可用";
        $("#capability-note").textContent = "你仍可以尝试开始，或检查后端是否已启动。";
        renderReadiness();
      });
  }

  function modelStatusText(model) {
    if (!model) return "模型信息暂不可用";
    if (model.status === "ready") return model.label + " 已就绪" + (model.size ? " · " + model.size : "");
    if (model.status === "downloading") return model.label + " 正在下载 · " + (model.progress || 0) + "%";
    if (model.status === "dependency_missing") return "本地依赖未安装 · 可切换云端";
    if (model.status === "runtime_download") return "首次启动时由本地运行时下载";
    if (model.status === "failed") return model.label + " 下载失败 · 可以重试";
    return model.label + " 尚未下载 · 首次使用约 " + (model.size || "需要一些空间");
  }

  function setRowState(row, status) {
    row.classList.remove("is-ready", "is-warning", "is-error", "is-busy");
    if (status === "ready") row.classList.add("is-ready");
    if (status === "downloading" || status === "not_downloaded" || status === "pending") row.classList.add("is-warning");
    if (status === "failed" || status === "dependency_missing" || status === "error") row.classList.add("is-error");
  }

  function formatBytes(bytes) {
    var value = Math.max(0, Number(bytes) || 0);
    if (value < 1024 * 1024) return Math.round(value / 1024) + "KB";
    return (value / (1024 * 1024)).toFixed(1) + "MB";
  }

  function setAudioReadiness(status, message, detail) {
    state.audioReadiness = status;
    var row = $("#audio-readiness-row");
    var statusNode = $("#audio-readiness-status");
    var check = $("#audio-readiness-check");
    if (!row || !statusNode || !check) return;
    statusNode.textContent = message;
    check.textContent = status === "ready" ? "✓" : status === "disabled" ? "—" : status === "error" ? "!" : "○";
    setRowState(row, status === "ready" ? "ready" : status === "error" ? "error" : "pending");
    $("#audio-storage-status").textContent = detail || message;
  }

  function checkAudioReadiness() {
    if (!state.saveAudio) {
      setAudioReadiness("disabled", "已关闭原声保存", "当前只保存字幕；你可以随时重新开启原声保存");
      renderReadiness();
      return Promise.resolve(true);
    }
    if (!window.MediaRecorder) {
      setAudioReadiness("error", "浏览器不支持原声录音", "请更新浏览器，或关闭原声保存后仅保存字幕");
      renderReadiness();
      return Promise.resolve(false);
    }
    if (!window.EchoAudioRepository || typeof window.EchoAudioRepository.getManifest !== "function") {
      setAudioReadiness("error", "本地音频存储不可用", "请检查浏览器存储权限，或关闭原声保存后仅保存字幕");
      renderReadiness();
      return Promise.resolve(false);
    }
    setAudioReadiness("pending", "正在检查本机存储", "正在检查本机存储空间");
    return Promise.all([
      window.EchoAudioRepository.getManifest("__echonote_audio_probe__"),
      window.EchoAudioRepository.getUsage()
    ]).then(function (values) {
      var usage = values[1];
      var detail = usage && usage.quota ? "原声会按片段持续保存在本机 · 已用 " + formatBytes(usage.usage) : "原声会按片段持续保存在本机浏览器";
      setAudioReadiness("ready", "本机原声保存可用", detail);
      renderReadiness();
      return true;
    }).catch(function () {
      setAudioReadiness("error", "本机音频存储不可用", "请检查浏览器存储权限，或关闭原声保存后仅保存字幕");
      renderReadiness();
      return false;
    });
  }

  function renderModelState() {
    var model = selectedModel();
    var row = $("#model-readiness-row");
    var statusNode = $("#model-readiness-status");
    var button = $("#model-download-button");
    var status = model ? model.status : "pending";
    var cloudReady = state.mode === "cloud" && state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured;
    setRowState(row, cloudReady ? "ready" : status);
    statusNode.textContent = state.mode === "cloud" ? (cloudReady ? "云端服务已配置，可以开始" : "云端尚未配置，请改用本地或配置服务") : modelStatusText(model);
    button.hidden = state.mode === "cloud" || !model || !model.download_supported || model.status === "ready" || model.status === "dependency_missing";
    if (!button.hidden) button.textContent = model.status === "downloading" ? "取消下载" : model.status === "failed" ? "重试下载" : "下载 " + model.label;
    modelSelect.querySelectorAll("option").forEach(function (option) {
      var item = state.models.find(function (entry) { return entry.id === option.value; });
      if (item) option.textContent = item.label + " · " + (item.status === "ready" ? "已就绪" : item.status === "downloading" ? (item.progress || 0) + "%" : item.size);
    });
  }

  function renderReadiness() {
    renderModelState();
    var micStatus = $("#mic-readiness-status");
    var micReady = state.micTestPassed;
    micStatus.textContent = micReady ? "已检测到声音，可以开始" : state.selectedDeviceId ? "已选择设备 · 建议先测试声音" : "浏览器默认麦克风 · 建议先测试声音";
    setRowState($("#mic-readiness-row"), micReady ? "ready" : "pending");
    $("#mic-readiness-check").textContent = micReady ? "✓" : "○";
    var connectionReady = state.connected;
    $("#connection-readiness-status").textContent = connectionReady ? "实时连接已就绪" : "等待后端连接";
    setRowState($("#connection-readiness-row"), connectionReady ? "ready" : "error");
    $("#connection-readiness-check").textContent = connectionReady ? "✓" : "!";
    var model = selectedModel();
    var modelReady = state.mode === "cloud" ? Boolean(state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured) : model && ["ready", "runtime_download"].indexOf(model.status) !== -1;
    var audioReady = !state.saveAudio || state.audioReadiness === "ready";
    var basicReady = Boolean(connectionReady && modelReady && audioReady);
    $("#readiness-summary").textContent = basicReady ? "可以开始" : "还需准备";
    $("#readiness-summary").classList.toggle("is-ready", basicReady);
    $("#readiness-panel").setAttribute("aria-busy", state.phase === "checking" ? "true" : "false");
  }

  function fetchModels() {
    return fetch("/api/models")
      .then(function (response) { if (!response.ok) throw new Error("models request failed"); return response.json(); })
      .then(function (data) {
        state.models = data.models || [];
        renderReadiness();
        var downloading = state.models.some(function (model) { return model.status === "downloading"; });
        if (downloading && !state.modelPollTimer) state.modelPollTimer = window.setInterval(fetchModels, 750);
        if (!downloading && state.modelPollTimer) { window.clearInterval(state.modelPollTimer); state.modelPollTimer = null; }
        return data;
      })
      .catch(function () {
        $("#model-readiness-status").textContent = "模型状态暂不可用，请检查后端";
        renderReadiness();
      });
  }

  function downloadSelectedModel() {
    var model = selectedModel();
    if (!model || state.mode === "cloud") return;
    var endpoint = model.status === "downloading" ? "/api/models/" + encodeURIComponent(model.id) + "/cancel" : "/api/models/" + encodeURIComponent(model.id) + "/download";
    fetch(endpoint, { method: "POST" })
      .then(function (response) {
        return response.json().then(function (data) { if (!response.ok && response.status !== 202) throw new Error(data.error || "模型操作失败"); return data; });
      })
      .then(function () { return fetchModels(); })
      .catch(function (error) { showError("模型操作失败", error.message, "download-model"); });
  }

  function addSegment(segment) {
    if (!segment || !segment.text) return;
    var isFinal = segment.is_final !== false;
    currentText.textContent = segment.text;
    currentSentence.classList.toggle("is-provisional", !isFinal);
    emptyState.hidden = true;
    if (!isFinal) return;
    state.segments.push(segment);
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
    scheduleSessionSave();
  }

  function clearTranscript() {
    $$(".transcript-segment").forEach(function (node) { node.remove(); });
    state.segments = [];
    emptyState.hidden = false;
    currentText.textContent = "开始听课后，当前句会在这里清晰显示。";
    currentSentence.classList.remove("is-provisional");
    returnLatest.hidden = true;
    $("#segment-count").textContent = "0";
  }

  function encodeBase64(arrayBuffer) {
    var bytes = new Uint8Array(arrayBuffer);
    var chunk = 0x8000;
    var binary = "";
    for (var i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
    return window.btoa(binary);
  }

  function emitAudioBuffer(buffer, sampleRate) {
    if (!state.recording || !state.socket || !state.connected || !buffer || !buffer.byteLength) return;
    state.socket.emit("audio_chunk", { audio: encodeBase64(buffer), sample_rate: sampleRate || (state.context ? state.context.sampleRate : 16000), sequence: state.sequence++ });
  }

  function sendAudioBuffer(buffer) {
    if (!state.recording || !state.socket || !state.connected || !buffer || !buffer.byteLength) return;
    var sampleRate = state.context ? state.context.sampleRate : 16000;
    state.audioBuffer.push(buffer, sampleRate).forEach(function (chunk) { emitAudioBuffer(chunk, sampleRate); });
  }

  function flushPendingAudio() {
    if (!state.audioBuffer) return;
    emitAudioBuffer(state.audioBuffer.flush(), state.context ? state.context.sampleRate : 16000);
  }

  function floatToPcm16(floatArray) {
    var pcm = new Int16Array(floatArray.length);
    for (var i = 0; i < floatArray.length; i += 1) {
      var sample = Math.max(-1, Math.min(1, floatArray[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return pcm.buffer;
  }

  function updateMicLevel(level) {
    var normalized = Math.max(0, Math.min(100, Math.round(level)));
    $("#mic-level-fill").style.width = normalized + "%";
    $("#mic-level").setAttribute("aria-valuenow", normalized);
    if (normalized > 8) $("#mic-level-label").textContent = "输入正常 · " + normalized + "%";
    return normalized;
  }

  function analyserLevel(analyser, data) {
    analyser.getByteTimeDomainData(data);
    var sum = 0;
    for (var i = 0; i < data.length; i += 1) {
      var sample = (data[i] - 128) / 128;
      sum += sample * sample;
    }
    return Math.min(100, Math.sqrt(sum / data.length) * 320);
  }

  function startLevelMonitor(analyser) {
    window.cancelAnimationFrame(state.levelRaf);
    state.levelAnalyser = analyser;
    state.levelData = new Uint8Array(analyser.fftSize);
    function tick() {
      if (!state.levelAnalyser) return;
      updateMicLevel(analyserLevel(state.levelAnalyser, state.levelData));
      state.levelRaf = window.requestAnimationFrame(tick);
    }
    tick();
  }

  function stopLevelMonitor() {
    window.cancelAnimationFrame(state.levelRaf);
    state.levelRaf = null;
    state.levelAnalyser = null;
    state.levelData = null;
  }

  async function beginCapture() {
    state.context = new (window.AudioContext || window.webkitAudioContext)();
    state.source = state.context.createMediaStreamSource(state.stream);
    state.levelAnalyser = state.context.createAnalyser();
    state.levelAnalyser.fftSize = 256;
    state.source.connect(state.levelAnalyser);
    startLevelMonitor(state.levelAnalyser);
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
    stopLevelMonitor();
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

  function microphoneConstraints() {
    var audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (state.selectedDeviceId) audio.deviceId = { exact: state.selectedDeviceId };
    return { audio: audio };
  }

  async function refreshMicrophones() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    var devices = await navigator.mediaDevices.enumerateDevices();
    var inputs = devices.filter(function (device) { return device.kind === "audioinput"; });
    var selected = state.selectedDeviceId;
    microphoneSelect.innerHTML = "";
    var defaultOption = document.createElement("option");
    defaultOption.value = "";
    defaultOption.textContent = "浏览器默认麦克风";
    microphoneSelect.appendChild(defaultOption);
    inputs.forEach(function (device, index) {
      var option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || "麦克风 " + (index + 1);
      microphoneSelect.appendChild(option);
    });
    if (selected && microphoneSelect.querySelector('[value="' + selected + '"]')) microphoneSelect.value = selected;
    else state.selectedDeviceId = microphoneSelect.value || "";
    renderReadiness();
  }

  function stopMicTest() {
    window.cancelAnimationFrame(state.micTestRaf);
    if (state.micTestStream) state.micTestStream.getTracks().forEach(function (track) { track.stop(); });
    if (state.micTestContext) state.micTestContext.close();
    state.micTestStream = null;
    state.micTestContext = null;
    state.micTestSource = null;
    state.micTestAnalyser = null;
    state.micTestRaf = null;
    $("#test-microphone").textContent = "测试麦克风";
  }

  async function testMicrophone() {
    if (state.micTestStream) { stopMicTest(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return showError("当前浏览器不支持麦克风", "请使用最新版 Chrome、Edge 或 Safari", "retry-mic");
    try {
      $("#test-microphone").textContent = "停止测试";
      state.micTestStream = await navigator.mediaDevices.getUserMedia(microphoneConstraints());
      await refreshMicrophones();
      state.micTestContext = new (window.AudioContext || window.webkitAudioContext)();
      state.micTestSource = state.micTestContext.createMediaStreamSource(state.micTestStream);
      state.micTestAnalyser = state.micTestContext.createAnalyser();
      state.micTestAnalyser.fftSize = 256;
      state.micTestSource.connect(state.micTestAnalyser);
      var data = new Uint8Array(state.micTestAnalyser.fftSize);
      var startedAt = Date.now();
      $("#mic-level-label").textContent = "请说话或拍手测试…";
      function tick() {
        if (!state.micTestAnalyser) return;
        var level = updateMicLevel(analyserLevel(state.micTestAnalyser, data));
        if (level > 8) {
          state.micTestPassed = true;
          $("#mic-level-label").textContent = "输入正常 · 可以开始";
          $("#mic-readiness-status").textContent = "已检测到声音，可以开始";
        } else if (Date.now() - startedAt > 1500 && !state.micTestPassed) {
          $("#mic-level-label").textContent = "没有检测到声音";
          $("#mic-readiness-status").textContent = "没有检测到麦克风声音，请换设备或重试";
        }
        state.micTestRaf = window.requestAnimationFrame(tick);
      }
      tick();
      renderReadiness();
    } catch (error) {
      stopMicTest();
      showError("没有获得麦克风权限", "请在浏览器地址栏允许麦克风后重试", "retry-mic", error.name);
    }
  }

  function persistSession(stopResult, audioManifest) {
    if (!window.indexedDB || !window.EchoStore) return Promise.resolve();
    var result = stopResult || {};
    state.audioManifest = audioManifest || state.audioManifest || null;
    var session = {
      id: result.session_id || state.sessionId || "session-" + Date.now(),
      title: $("#course-title").value.trim() || "未命名课堂",
      createdAt: state.sessionStartedAt ? new Date(state.sessionStartedAt).toISOString() : new Date().toISOString(),
      durationMs: state.sessionStartedAt ? Math.round(Date.now() - state.sessionStartedAt) : 0,
      language: state.language,
      provider: result.provider || state.provider || "unknown",
      model: result.model || state.model || "",
      segments: result.segments || state.segments,
      audio: state.saveAudio ? state.audioManifest : null
    };
    state.sessionId = session.id;
    return window.EchoStore.saveSession(session).catch(function () { $("#feed-hint").textContent = "课堂仍在进行，但本地保存暂不可用"; });
  }

  function scheduleSessionSave() {
    if (!state.sessionId || !window.EchoStore) return;
    window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(function () { persistSession(); }, 500);
  }

  function completeSession(result, audioManifest) {
    if (!state.finishing || state.finalizing) return;
    state.finalizing = true;
    if (result && result.segments) {
      state.segments = [];
      $$(".transcript-segment").forEach(function (node) { node.remove(); });
      result.segments.forEach(addSegment);
    }
    return persistSession(result || {}, audioManifest).then(function () {
      releaseCapture();
      state.audioRecorder = null;
      setRecordingUi(false);
      window.clearTimeout(state.saveTimer);
      state.saveTimer = null;
      state.finishing = false;
      state.finalizing = false;
      state.audioStopPromise = null;
      state.stopResult = null;
      var audioMessage = !state.saveAudio ? "字幕已保存" : audioManifest && audioManifest.status === "ready" ? "字幕和原声已保存" : audioManifest ? "字幕已保存，原声部分保存" : "字幕已保存，原声未保存";
      setAppPhase("saved", "本次听课已保存", audioMessage + " · 可以前往课后复习");
      hideError();
      renderReadiness();
    });
  }

  function receiveStopResult(result) {
    if (!state.finishing || state.stopResult) return;
    state.stopResult = result || {};
    if (!state.audioStopPromise) state.audioStopPromise = Promise.resolve(null);
    Promise.resolve(state.audioStopPromise).then(function (audioManifest) {
      return completeSession(state.stopResult, audioManifest);
    });
  }

  function readyToStart() {
    if (state.mode === "cloud") return Boolean(state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured);
    var model = selectedModel();
    return Boolean(model && ["ready", "runtime_download"].indexOf(model.status) !== -1);
  }

  async function startListening() {
    if (state.recording) return stopListening();
    if (state.starting || state.finishing) return;
    if (!state.socket || !state.connected) return showError("实时连接尚未就绪", "请确认后端已启动", "retry-connection");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return showError("当前浏览器不支持麦克风", "请使用最新版 Chrome、Edge 或 Safari", "retry-mic");
    var model = selectedModel();
    var outgoingMode = state.mode;
    if (!readyToStart()) {
      if (state.mode === "auto" && state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured) {
        return showError("本地模型还没有准备好", "自动模式将使用已配置云端并发送音频；请先明确切换到云端。", "switch-cloud");
      }
      return showError("模型还没有准备好", model ? modelStatusText(model) : "请选择一个可用模型", "download-model", "MODEL_NOT_READY");
    }
    if (state.saveAudio && !(await checkAudioReadiness())) return showError("原声保存不可用", "请关闭“保存原声”后仅保存字幕，或检查浏览器存储权限", "disable-audio", "AUDIO_STORAGE_UNAVAILABLE");
    state.starting = true;
    hideError();
    stopMicTest();
    setAppPhase("preparing", "正在准备麦克风", "请在浏览器提示中允许麦克风");
    try {
      state.stream = await navigator.mediaDevices.getUserMedia(microphoneConstraints());
      await refreshMicrophones();
      if (state.mode === "auto" && model && model.status !== "ready") outgoingMode = "cloud";
      var payload = { mode: outgoingMode, model: modelSelect.value, language: $("#language-select").value, sample_rate: 16000, enable_vad: true };
      setAppPhase("preparing", "正在加载模型", model ? modelStatusText(model) : "正在启动转录服务");
      var completed = false;
      state.startTimer = window.setTimeout(function () {
        if (completed) return;
        completed = true;
        state.starting = false;
        releaseCapture();
        showError("模型准备时间较长", "可以等待模型下载完成，或切换到 Tiny / 云端后重试", "download-model", "MODEL_LOAD_TIMEOUT");
      }, 45000);
      state.socket.emit("start_transcription", payload, async function (result) {
        if (completed) return;
        completed = true;
        window.clearTimeout(state.startTimer);
        state.startTimer = null;
        state.starting = false;
        if (!result || result.status !== "success") {
          releaseCapture();
          return showError(result && result.error ? result.error.message : "无法开始转录", result && result.error ? result.error.action : "请切换推理路径后重试", "download-model", result && result.error ? result.error.code : "START_FAILED");
        }
        state.sequence = 0;
        state.audioBuffer.reset();
        state.sessionStartedAt = Date.now();
        state.sessionId = result.session_id;
        state.provider = result.provider;
        state.model = result.model || payload.model;
        state.language = payload.language;
        state.audioRecorder = null;
        state.audioManifest = null;
        $("#model-status").textContent = (result.provider || "provider") + (result.model ? " · " + result.model : "");
        try {
          await beginCapture();
          if (state.saveAudio) {
            state.audioRecorder = window.EchoAudioRecorder.create({
              stream: state.stream,
              sessionId: state.sessionId,
              repository: window.EchoAudioRepository,
              now: window.performance && typeof window.performance.now === "function" ? window.performance.now.bind(window.performance) : Date.now
            });
            state.audioManifest = await state.audioRecorder.start();
          }
          setRecordingUi(true);
        } catch (captureError) {
          state.audioBuffer.reset();
          releaseCapture();
          state.audioRecorder = null;
          state.socket.emit("stop_transcription", {});
          var audioFailure = state.saveAudio && /AUDIO|MEDIA_RECORDER/.test(String(captureError && captureError.code || ""));
          showError(audioFailure ? "原声保存不可用" : "麦克风启动失败", audioFailure ? "请关闭“保存原声”后重试；本次没有开始转录" : captureError.message, audioFailure ? "disable-audio" : "retry-mic", captureError.code || "MIC_CAPTURE_FAILED");
        }
      });
    } catch (error) {
      state.starting = false;
      releaseCapture();
      showError("没有获得麦克风权限", "请在浏览器地址栏允许麦克风后重试", "retry-mic", error.name);
    }
  }

  function stopListening() {
    if (!state.recording || state.finishing) return;
    state.finishing = true;
    state.finalizing = false;
    state.stopResult = null;
    setAppPhase("stopping", "正在保存课堂", "正在处理最后一小段音频");
    flushPendingAudio();
    state.audioStopPromise = state.audioRecorder ? state.audioRecorder.stop().catch(function (caught) {
      return { sessionId: state.sessionId, enabled: true, status: "partial", storage: "none", mimeType: "", extension: "", chunkCount: 0, bytes: 0, durationMs: 0, error: caught.message || "原声保存失败" };
    }) : Promise.resolve(null);
    if (state.socket && state.connected) {
      state.socket.emit("stop_transcription", {}, receiveStopResult);
    } else {
      receiveStopResult({ session_id: state.sessionId, provider: state.provider, model: state.model, segments: state.segments });
      showError("实时连接已断开", "本次字幕仍会保存；原声将按已写入的片段保留", "retry-connection");
    }
  }

  function updatePrivacyCopy() {
    var pathCopy = state.mode === "cloud"
      ? "云端模式会发送实时处理所需音频到已配置服务，但不会建立云端录音归档。"
      : state.mode === "local"
        ? "本地模式只在你的设备上处理音频，适合重视隐私的课堂。"
        : "自动模式会优先使用已准备好的本地模型；本地不可用时，需明确切换到云端。";
    var audioCopy = state.saveAudio ? "原声和字幕只保存在本机。" : "当前已关闭原声保存，只保留字幕。";
    $("#privacy-copy").textContent = pathCopy + " " + audioCopy;
  }

  function setupAudioSave() {
    var checkbox = $("#save-audio");
    checkbox.addEventListener("change", function () {
      state.saveAudio = checkbox.checked;
      savePreferences();
      updatePrivacyCopy();
      checkAudioReadiness();
      renderReadiness();
    });
    checkAudioReadiness();
  }

  function setupModes() {
    $$(".mode-option").forEach(function (button) {
      button.addEventListener("click", function () {
        if (state.recording || state.starting) return;
        state.mode = button.dataset.mode;
        $$(".mode-option").forEach(function (option) { option.classList.toggle("is-selected", option === button); option.setAttribute("aria-pressed", option === button ? "true" : "false"); });
        updatePrivacyCopy();
        savePreferences();
        renderReadiness();
      });
    });
  }

  function setupMicrophone() {
    microphoneSelect.addEventListener("change", function () {
      state.selectedDeviceId = microphoneSelect.value;
      state.micTestPassed = false;
      $("#mic-level-label").textContent = "尚未测试";
      savePreferences();
      renderReadiness();
    });
    $("#test-microphone").addEventListener("click", testMicrophone);
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) navigator.mediaDevices.addEventListener("devicechange", refreshMicrophones);
    refreshMicrophones().catch(function () { /* permission may be pending */ });
  }

  function setupSocket() {
    if (!window.io) return showError("实时连接脚本未加载", "联网加载 Socket.IO 失败，请检查网络或将客户端脚本本地化", "retry-connection");
    state.socket = window.io({ transports: ["websocket", "polling"] });
    state.socket.on("connect", function () { state.connected = true; if (!state.recording && !state.starting) setAppPhase("idle", "实时连接已就绪", $("#model-status").textContent); renderReadiness(); });
    state.socket.on("disconnect", function () { state.connected = false; renderReadiness(); if (state.recording) showError("实时连接中断", "请先结束当前会话；网络恢复后可以重新开始", "retry-connection", "SOCKET_DISCONNECTED"); else setAppPhase("error", "实时连接已断开", "请检查后端服务"); });
    state.socket.on("transcript_segment", addSegment);
    state.socket.on("transcription_error", function (error) { showError(error.message || "转录出现问题", error.action, "", error.code); });
    state.socket.on("transcription_stopped", function (result) { if (state.recording || state.finishing) receiveStopResult(result); });
  }

  function setupHelp() {
    var dialog = $("#help-dialog");
    $("#help-button").addEventListener("click", function () { if (dialog.showModal) dialog.showModal(); });
    $("#close-help").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
  }

  function setupTranscriptFollow() {
    feed.addEventListener("scroll", function () {
      var atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 24;
      returnLatest.hidden = atBottom;
      if (!atBottom) $("#autoscroll-toggle").checked = false;
    });
    returnLatest.addEventListener("click", function () { feed.scrollTo({ top: feed.scrollHeight, behavior: "smooth" }); $("#autoscroll-toggle").checked = true; returnLatest.hidden = true; });
    $("#autoscroll-toggle").addEventListener("change", function () { if (this.checked) { feed.scrollTo({ top: feed.scrollHeight, behavior: "smooth" }); returnLatest.hidden = true; } });
  }

  $("#model-download-button").addEventListener("click", downloadSelectedModel);
  $("#model-select").addEventListener("change", function () { savePreferences(); renderReadiness(); });
  $("#language-select").addEventListener("change", savePreferences);
  $("#course-title").addEventListener("input", savePreferences);
  $("#error-action").addEventListener("click", function () {
    var action = this.dataset.action;
    if (action === "download-model") downloadSelectedModel();
    else if (action === "retry-mic") testMicrophone();
    else if (action === "switch-cloud") { $(".mode-option[data-mode='cloud']").click(); hideError(); }
    else if (action === "disable-audio") { $("#save-audio").checked = false; $("#save-audio").dispatchEvent(new Event("change")); hideError(); }
    else if (action === "retry-connection") window.location.reload();
  });
  startButton.addEventListener("click", startListening);
  $("#clear-transcript").addEventListener("click", clearTranscript);
  restorePreferences();
  setupModes();
  setupAudioSave();
  updatePrivacyCopy();
  setupMicrophone();
  setupHelp();
  setupTranscriptFollow();
  setupSocket();
  setAppPhase("checking", "正在检查听课条件", "马上告诉你是否可以开始");
  Promise.all([loadCapabilities(), fetchModels()]).then(function () { setAppPhase(state.connected ? "idle" : "checking", state.connected ? "实时连接已就绪" : "等待实时连接", $("#model-status").textContent); renderReadiness(); });
  window.startLecture = startListening;
  window.stopLecture = stopListening;
  document.addEventListener("keydown", function (event) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (event.key === " " && tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA") { event.preventDefault(); startListening(); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#course-title").focus(); }
  });
})();
