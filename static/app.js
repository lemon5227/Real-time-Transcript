/* 拾句 live classroom workbench. No framework required: the page stays fast on thin laptops. */
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
  var translationModeSelect = $("#translation-mode");
  var translationProviderSelect = $("#translation-provider");
  var translationTargetSelect = $("#translation-target");
  var translationModelModeSelect = $("#translation-model-mode");
  var settingsDrawer = $("#settings-drawer");
  var quickTranslationToggle = $("#quick-translation-toggle");
  var quickSettings = $("#quick-settings");
  var quickRailToggle = $("#toggle-quick-settings");
  var quickStatusLights = document.querySelectorAll(".quick-rail-status-light");
  var lectureIntro = $("#lecture-intro");
  var lectureIntroToggle = $("#toggle-lecture-intro");
  var lectureIntroCompact = $("#lecture-intro-compact");
  var lectureIntroCompactToggle = $("#toggle-lecture-intro-compact");
  // Fallback only: the real value arrives with /api/capabilities and matches the
  // backend provider startup timeout.
  var MAX_PENDING_AUDIO_SECONDS = 30;
  var SETTINGS_KEY = "echonote-preferences-v1";
  var UI_STATE_KEY = "echonote-ui-state-v1";
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
    pendingAudio: [],
    pendingAudioSeconds: 0,
    pendingAudioLimitSeconds: MAX_PENDING_AUDIO_SECONDS,
    sequence: 0,
    segments: [],
    liveSegment: null,
    liveArticle: null,
    sessionStartedAt: null,
    captureStartedAtMs: null,
    timer: null,
    capabilities: null,
    models: [],
    modelPollTimer: null,
    followPulseTimer: null,
    followScrollLock: false,
    followScrollUnlockTimer: null,
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
    translationStopPromise: null,
    translationQueue: null,
    translationMode: "off",
    translationProvider: "microsoft",
    translationTarget: "zh",
    translationModelMode: "auto",
    translationNotice: "",
    pendingModelId: null,
    transcriptionReady: false,
    transcriptionFailed: false,
    backpressureTimer: null,
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
    var busy = phase === "checking" || (phase === "preparing" && !state.recording) || phase === "stopping";
    var recording = phase !== "stopping" && (phase === "recording" || state.recording);
    startButton.disabled = busy;
    startButton.classList.toggle("is-busy", busy);
    startButton.setAttribute("aria-busy", busy ? "true" : "false");
    $("#readiness-panel").setAttribute("aria-busy", phase === "checking" ? "true" : "false");
    if (phase === "capturing") {
      startLabel.textContent = "结束听课";
      setStatus("busy", message || "正在收音 · 模型准备中", detail || "模型启动后会补处理已录音频");
    } else if (phase === "recording") {
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
    } else if (phase === "waiting") {
      startLabel.textContent = "开始听课";
      setStatus("busy", message || "正在等待实时连接", detail || "实时连接问题会显示在课前检查");
    } else if (phase === "checking") {
      startLabel.textContent = "检查中…";
      setStatus("busy", message || "正在检查听课条件", detail || "马上告诉你是否可以开始");
    }
    $("#recording-state").textContent = recording ? phase === "capturing" ? "正在收音 · 模型准备中" : "正在收音与转录" : phase === "preparing" ? message || "正在准备" : phase === "stopping" ? "正在保存课堂" : phase === "error" ? "需要处理后再试" : "麦克风已就绪";
    syncQuickSettings();
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
    if (recording) document.dispatchEvent(new CustomEvent("echonote:recording-started"));
    startButton.classList.toggle("is-recording", recording);
    $("#sound-bars").classList.toggle("is-active", recording);
    if (recording) {
      if (state.transcriptionFailed) {
        setAppPhase("error", "转录暂不可用 · 原声仍在保存", "结束后仍可回听本次课堂录音");
      } else {
        setAppPhase(
          state.transcriptionReady ? "recording" : "capturing",
          state.transcriptionReady ? "正在实时转录" : "正在收音 · 模型准备中",
          state.transcriptionReady ? $("#model-status").textContent : "模型启动后会补处理已录音频"
        );
      }
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
    if (/MLX_LANGUAGE|RUNTIME_MISMATCH/.test(value)) return "switch-cloud";
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

  function getUiPreferences() {
    try { return JSON.parse(window.localStorage.getItem(UI_STATE_KEY) || "{}"); } catch (_error) { return {}; }
  }

  function saveUiPreferences(changes) {
    try {
      var preferences = getUiPreferences();
      Object.keys(changes).forEach(function (key) { preferences[key] = changes[key]; });
      window.localStorage.setItem(UI_STATE_KEY, JSON.stringify(preferences));
    } catch (_error) { /* localStorage is optional */ }
  }

  function savePreferences() {
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        mode: state.mode,
        model: modelSelect.value,
        language: $("#language-select").value,
        courseTitle: $("#course-title").value.trim(),
        glossary: $("#glossary-input").value.trim(),
        deviceId: state.selectedDeviceId,
        saveAudio: state.saveAudio,
        translationMode: state.translationMode,
        translationProvider: state.translationProvider,
        translationTarget: state.translationTarget,
        translationModelMode: state.translationModelMode
      }));
    } catch (_error) { /* localStorage is optional */ }
  }

  function restorePreferences() {
    var saved = getSavedPreferences();
    if (["auto", "local", "cloud"].indexOf(saved.mode) !== -1) state.mode = saved.mode;
    if (saved.model && modelSelect.querySelector('[value="' + saved.model + '"]')) modelSelect.value = saved.model;
    if (saved.language && $("#language-select").querySelector('[value="' + saved.language + '"]')) $("#language-select").value = saved.language;
    if (saved.courseTitle) $("#course-title").value = saved.courseTitle;
    if (saved.glossary) $("#glossary-input").value = saved.glossary;
    state.saveAudio = saved.saveAudio !== false;
    $("#save-audio").checked = state.saveAudio;
    if (["off", "fast", "precise", "auto"].indexOf(saved.translationMode) !== -1) state.translationMode = saved.translationMode;
    if (["google", "microsoft"].indexOf(saved.translationProvider) !== -1) state.translationProvider = saved.translationProvider;
    if (saved.translationTarget && translationTargetSelect.querySelector('[value="' + saved.translationTarget + '"]')) state.translationTarget = saved.translationTarget;
    if (["auto", "local", "cloud"].indexOf(saved.translationModelMode) !== -1) state.translationModelMode = saved.translationModelMode;
    translationModeSelect.value = state.translationMode;
    translationProviderSelect.value = state.translationProvider;
    translationTargetSelect.value = state.translationTarget;
    translationModelModeSelect.value = state.translationModelMode;
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

  function renderSettingsOverview() {
    var modeSummary = $("#settings-mode-summary");
    var modeNote = $("#settings-mode-note");
    var deviceSummary = $("#settings-device-summary");
    var deviceNote = $("#settings-device-note");
    if (!modeSummary || !modeNote || !deviceSummary || !deviceNote) return;
    var mode = state.mode === "local" ? "本地" : state.mode === "cloud" ? "云端" : "自动";
    modeSummary.textContent = mode;
    modeNote.textContent = state.mode === "auto" ? "按设备选择最合适路径" : state.mode === "local" ? "音频留在本机处理" : "适合本地性能有限的设备";
    var runtime = localRuntime();
    var device = state.capabilities && state.capabilities.local ? state.capabilities.local.device : null;
    deviceSummary.textContent = runtime === "mlx" ? "Apple Silicon" : runtime === "cuda" ? "NVIDIA GPU" : runtime === "cpu" ? "CPU" : "检测中";
    deviceNote.textContent = device && device.performance ? "性能档位 · " + device.performance : runtime ? runtimeCopy(runtime) : "正在读取设备能力";
  }

  function syncQuickSettings() {
    renderSettingsOverview();
    if (quickTranslationToggle) {
      var translationOn = translationModeSelect.value !== "off";
      quickTranslationToggle.checked = translationOn;
      var translationState = $("#quick-translation-state");
      var translationDetail = $("#quick-translation-detail");
      if (translationState) translationState.textContent = translationOn ? "已开启" : "关闭";
      if (translationDetail) translationDetail.textContent = state.translationNotice || (translationOn ? "快速辅助 · 课后可精确翻译" : "关闭时不占用资源 · 中文复习辅助");
    }
    var model = selectedModel();
    var quickModel = $("#quick-rail-model");
    var quickRuntime = $("#quick-rail-runtime");
    var quickAudio = $("#quick-rail-audio");
    var quickSession = $("#quick-rail-session-state");
    var quickStatusLight = $("#quick-rail-status-light");
    if (quickModel) quickModel.textContent = model ? model.label : "等待模型信息";
    if (quickRuntime) quickRuntime.textContent = state.mode === "cloud" ? "云端模型" : runtimeCopy(localRuntime());
    if (quickAudio) quickAudio.textContent = state.saveAudio ? "仅保存在本机" : "仅保存字幕";
    var statusKind = "idle";
    var sessionCopy = "等待连接";
    if (state.phase === "capturing") {
      statusKind = "busy";
      sessionCopy = "正在收音 · 模型准备中";
    } else if (state.recording || state.phase === "recording") {
      statusKind = "live";
      sessionCopy = "实时转录中";
    } else if (["checking", "preparing", "stopping", "waiting"].indexOf(state.phase) !== -1) {
      statusKind = "busy";
      sessionCopy = state.phase === "stopping" ? "正在保存" : "准备中";
    } else if (state.phase === "error") {
      statusKind = "error";
      sessionCopy = "需要处理";
    } else if (state.phase === "saved") {
      statusKind = "ready";
      sessionCopy = "已保存 · 可复习";
    } else if (state.connected) {
      statusKind = "ready";
      sessionCopy = "已连接 · 等待开始";
    }
    if (quickSession) {
      quickSession.textContent = sessionCopy;
      quickSession.dataset.status = statusKind;
    }
    Array.prototype.forEach.call(quickStatusLights, function (light) { light.dataset.status = statusKind; });
  }

  var PARAKEET_LANGUAGES = ["bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr", "hu", "it", "lt", "lv", "mt", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "uk"];

  function localRuntime() {
    return state.capabilities && state.capabilities.local ? state.capabilities.local.runtime : "";
  }

  function runtimeCopy(runtime) {
    if (runtime === "mlx") return "MLX · Apple Silicon";
    if (runtime === "cuda") return "CUDA · NVIDIA GPU";
    if (runtime === "cpu") return "CPU · 可用云端兜底";
    return "运行时检测中";
  }

  function modelRuntimeCompatible(model) {
    if (!model || !localRuntime()) return true;
    return localRuntime() === "mlx" ? model.runtime === "mlx" : model.runtime !== "mlx";
  }

  function modelLanguageCompatible(model) {
    if (!model || model.runtime !== "mlx") return true;
    var language = $("#language-select").value;
    return language === "auto" || PARAKEET_LANGUAGES.indexOf(language) !== -1;
  }

  function localModelUsable(model) {
    return Boolean(
      model &&
      modelRuntimeCompatible(model) &&
      modelLanguageCompatible(model) &&
      ["ready", "runtime_download"].indexOf(model.status) !== -1
    );
  }

  function syncRuntimeModelSelection() {
    if (!state.models.length || !localRuntime()) return;
    var current = selectedModel();
    var compatible = function (model) { return modelRuntimeCompatible(model); };
    if (!current || !compatible(current)) {
      var recommended = state.capabilities.local.recommended_model;
      var replacement = state.models.find(function (model) { return model.id === recommended && compatible(model); }) || state.models.find(compatible);
      if (replacement) modelSelect.value = replacement.id;
    }
    modelSelect.querySelectorAll("option").forEach(function (option) {
      var item = state.models.find(function (model) { return model.id === option.value; });
      option.disabled = state.mode !== "cloud" && Boolean(item && !compatible(item));
    });
  }

  function pulseFollowControl() {
    var control = $("#autoscroll-toggle").closest(".toggle-label");
    if (!control) return;
    control.classList.remove("is-pulsing");
    window.requestAnimationFrame(function () {
      control.classList.add("is-pulsing");
      window.clearTimeout(state.followPulseTimer);
      state.followPulseTimer = window.setTimeout(function () { control.classList.remove("is-pulsing"); }, 650);
    });
  }

  function protectFollowScroll() {
    state.followScrollLock = true;
    window.clearTimeout(state.followScrollUnlockTimer);
    state.followScrollUnlockTimer = window.setTimeout(function () {
      state.followScrollLock = false;
    }, 220);
  }

  function scrollToLatest() {
    protectFollowScroll();
    var previousBehavior = feed.style.scrollBehavior;
    feed.style.scrollBehavior = "auto";
    if (feed.scrollTo) feed.scrollTo({ top: feed.scrollHeight, behavior: "auto" });
    else feed.scrollTop = feed.scrollHeight;
    feed.style.scrollBehavior = previousBehavior;
  }

  function updateFollowUi() {
    var toggle = $("#autoscroll-toggle");
    var control = toggle.closest(".toggle-label");
    var stateNode = $("#autoscroll-state");
    if (!toggle || !control) return;
    var enabled = toggle.checked;
    control.classList.toggle("is-active", enabled);
    control.setAttribute("aria-label", enabled ? "自动跟随已开启" : "自动跟随已关闭");
    if (stateNode) stateNode.textContent = enabled ? "跟随中" : "已暂停";
    feed.classList.toggle("is-auto-following", enabled);
  }

  function renderModelGuidance(model) {
    var badge = $("#model-profile-badge");
    var chip = $("#model-ready-chip");
    var speed = $("#model-speed");
    var quality = $("#model-quality");
    var resource = $("#model-resource");
    var summary = $("#model-selection-summary");
    if (!badge || !chip || !speed || !quality || !resource || !summary) return;
    if (!model) {
      badge.textContent = "等待模型信息";
      chip.textContent = "不可用";
      speed.textContent = quality.textContent = resource.textContent = "—";
      summary.textContent = "暂时无法读取模型目录；你仍可以检查后端连接。";
      return;
    }
    var ready = model.status === "ready" || model.status === "runtime_download";
    var runtimeMismatch = state.mode !== "cloud" && !modelRuntimeCompatible(model);
    var languageMismatch = state.mode !== "cloud" && !modelLanguageCompatible(model);
    badge.textContent = state.mode === "cloud" ? "云端转录" : (model.best_for || model.languages || "本地模型");
    chip.textContent = state.mode === "cloud" ? "由服务配置" : runtimeMismatch ? "设备不匹配" : languageMismatch ? "语言需云端" : ready ? "已就绪" : model.status === "downloading" ? "下载中" : model.status === "dependency_missing" ? "缺少依赖" : "未下载";
    chip.classList.toggle("is-ready", (ready && !runtimeMismatch && !languageMismatch) || state.mode === "cloud");
    chip.classList.toggle("is-warning", (!ready || runtimeMismatch || languageMismatch) && state.mode !== "cloud");
    speed.textContent = state.mode === "cloud" ? "取决于网络" : (model.speed || "—");
    quality.textContent = model.quality || "—";
    resource.textContent = state.mode === "cloud" ? "本机低" : (model.resource || "—");
    summary.textContent = state.mode === "cloud"
      ? "云端路径不占用本机推理算力；实际延迟取决于网络和云端服务。"
      : runtimeMismatch
        ? (localRuntime() === "mlx" ? "这台 Mac 只使用 MLX 模型；请选择 Parakeet TDT v3。" : "这是 Mac MLX 模型，请选择当前设备支持的 Whisper 模型。")
        : languageMismatch
          ? "Parakeet 当前适合英语和欧洲语言课堂；中文课程请切换到云端。"
      : (model.languages || "多语言") + " · " + (model.size || "需要模型文件") + " · 适合：" + (model.best_for || "通用转录");
  }

  function updateCapabilityUi(data) {
    state.capabilities = data;
    // How long audio may be held while the model loads. Read from the backend so
    // the browser buffer and the provider startup timeout cannot drift apart.
    if (data.audio && Number(data.audio.startup_timeout_seconds) > 0) {
      state.pendingAudioLimitSeconds = Number(data.audio.startup_timeout_seconds);
    }
    var localAvailable = Boolean(data.local && data.local.available);
    var cloudAvailable = Boolean(data.cloud && data.cloud.configured);
    var runtime = data.local && data.local.runtime ? data.local.runtime : "";
    var readyCount = data.local && Array.isArray(data.local.ready_models) ? data.local.ready_models.length : 0;
    var title = localAvailable ? (runtime === "mlx" ? "Mac MLX 本地路径可用" : runtime === "cuda" ? "CUDA GPU 本地路径可用" : "CPU 本地路径可用") : cloudAvailable ? "云端路径已就绪" : "需要准备一个转录路径";
    var note = localAvailable
      ? runtimeCopy(runtime) + " · 已准备 " + readyCount + " 个本地模型 · 推荐 " + (data.local.recommended_model || "small")
      : cloudAvailable
        ? "本地模型不可用时可以使用已配置云端"
        : "安装本地依赖，或在 .env 中配置云端模型";
    $("#capability-title").textContent = title;
    $("#capability-note").textContent = note;
    $("#runtime-badge").textContent = runtimeCopy(runtime);
    $("#runtime-badge").className = "runtime-badge" + (runtime ? " is-" + runtime : "");
    $("#model-status").textContent = localAvailable ? "本地能力已检查" : cloudAvailable ? "云端能力已检查" : "等待模型配置";
    $("#capability-panel").classList.toggle("is-warning", !localAvailable && !cloudAvailable);
    updateTranslationUi();
    renderReadiness();
  }

  function translationCapability(provider) {
    var translation = state.capabilities && state.capabilities.translation;
    return translation && translation[provider] ? translation[provider] : { configured: false };
  }

  function translationReady() {
    if (state.translationMode === "off") return true;
    if (state.translationMode === "fast") {
      var fastCapability = translationCapability(state.translationProvider);
      return Boolean(fastCapability.configured || fastCapability.public_fallback);
    }
    var local = translationCapability("local_model");
    var cloud = translationCapability("cloud_model");
    if (state.translationModelMode === "local") return Boolean(local.configured);
    if (state.translationModelMode === "cloud") return Boolean(cloud.configured);
    return Boolean(local.configured || cloud.configured);
  }

  function updateTranslationUi() {
    var node = $("#translation-status");
    if (!node) return;
    var mode = state.translationMode;
    var google = translationCapability("google");
    var provider = mode === "fast" ? state.translationProvider === "microsoft" ? "Microsoft 快速翻译" : google.configured ? "Google Cloud 快速翻译" : "Google 公共翻译 · 免 Key" : mode === "off" ? "实时翻译默认关闭；课后可以再翻译" : state.translationModelMode === "local" ? "本地精确翻译" : state.translationModelMode === "cloud" ? "云端精确翻译" : "自动选择精确模型";
    var ready = translationReady();
    node.textContent = ready ? provider + " · 目标：" + (translationTargetSelect.options[translationTargetSelect.selectedIndex] ? translationTargetSelect.options[translationTargetSelect.selectedIndex].textContent : "中文") : provider + "尚未配置，课堂仍可只做原文转录";
    node.classList.toggle("is-warning", !ready && mode !== "off");
  }

  function loadCapabilities() {
    return fetch("/api/capabilities")
      .then(function (response) { if (!response.ok) throw new Error("capabilities request failed"); return response.json(); })
      .then(updateCapabilityUi)
      .catch(function () {
        $("#capability-title").textContent = "设备检测暂不可用";
        $("#capability-note").textContent = "你仍可以尝试开始，或检查后端是否已启动。";
        $("#runtime-badge").textContent = "检测失败";
        renderReadiness();
      });
  }

  function modelStatusText(model) {
    if (!model) return "模型信息暂不可用";
    if (state.mode !== "cloud" && !modelRuntimeCompatible(model)) return localRuntime() === "mlx" ? "当前 Mac 只使用 MLX Parakeet，请重新选择模型" : "当前设备不支持 Mac MLX 模型";
    if (state.mode !== "cloud" && !modelLanguageCompatible(model)) return "Parakeet 当前不支持这门语言，请切换到云端";
    if (model.status === "ready") return model.label + " 已就绪" + (model.size ? " · " + model.size : "");
    if (model.status === "downloading") return model.label + " 正在下载 · " + (model.progress || 0) + "%";
    if (model.status === "dependency_missing") return model.weights_available ? "模型文件已在本机 · 需要安装本地依赖" : model.download_supported ? "本地依赖未安装 · 仍可先下载模型" : "本地依赖未安装 · 可切换云端";
    if (model.status === "runtime_download") return "首次启动时由本地运行时下载";
    if (model.status === "failed") return model.label + " 下载失败 · 可以重试";
    return model.label + " 尚未下载 · 首次使用约 " + (model.size || "需要一些空间");
  }

  function setRowState(row, status) {
    row.classList.remove("is-ready", "is-warning", "is-error", "is-busy");
    if (status === "ready") row.classList.add("is-ready");
    if (status === "downloading" || status === "not_downloaded" || status === "pending") row.classList.add("is-warning");
    if (status === "failed" || status === "dependency_missing" || status === "error" || status === "incompatible") row.classList.add("is-error");
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
    var incompatible = state.mode !== "cloud" && model && (!modelRuntimeCompatible(model) || !modelLanguageCompatible(model));
    var cloudReady = state.mode === "cloud" && state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured;
    renderModelGuidance(model);
    setRowState(row, cloudReady ? "ready" : incompatible ? "incompatible" : status);
    statusNode.textContent = state.mode === "cloud" ? (cloudReady ? "云端服务已配置，可以开始" : "云端尚未配置，请改用本地或配置服务") : modelStatusText(model);
    var canDownload = model && model.download_supported && model.status !== "ready" && !(model.status === "dependency_missing" && model.weights_available);
    button.hidden = state.mode === "cloud" || incompatible || !canDownload;
    if (!button.hidden) button.textContent = model.status === "downloading" ? "取消下载" : model.status === "failed" ? "重试下载" : "下载 " + model.label;
    modelSelect.querySelectorAll("option").forEach(function (option) {
      var item = state.models.find(function (entry) { return entry.id === option.value; });
      if (item) option.textContent = item.label + " · " + (item.runtime === "mlx" ? "MLX" : "标准") + " · " + (item.status === "ready" ? "已就绪" : item.status === "downloading" ? (item.progress || 0) + "%" : item.size);
    });
    syncRuntimeModelSelection();
    renderModelManagement();
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function modelRuntimeLabel(runtime) {
    if (runtime === "mlx") return "MLX";
    if (runtime === "standard") return "CPU / CUDA";
    return runtime || "本地";
  }

  function modelManagerStatus(model) {
    var runtimeMismatch = arguments.length > 1 && arguments[1];
    if (runtimeMismatch) return "当前设备不适配此运行时";
    if (model.status === "ready") return "已下载 · 可以使用";
    if (model.status === "downloading") return "下载中 · " + (model.progress || 0) + "%";
    if (model.status === "failed") return "下载失败 · 可以重试";
    if (model.status === "dependency_missing" && model.weights_available) return "文件已在本机 · 需要运行依赖";
    if (model.status === "dependency_missing" && model.download_supported) return "运行依赖未安装 · 仍可先下载";
    if (model.status === "dependency_missing") return "需要安装本地运行依赖";
    if (model.status === "runtime_download") return "首次启动时自动下载";
    return "尚未下载 · 可提前准备";
  }

  function modelManagerStatusKind(model, runtimeMismatch) {
    if (runtimeMismatch) return "mismatch";
    if (model.status === "ready") return "ready";
    if (model.status === "downloading") return "busy";
    if (model.status === "failed") return "error";
    if (model.status === "dependency_missing") return "blocked";
    if (model.status === "runtime_download") return "managed";
    return "idle";
  }

  function renderModelLibraryHero() {
    var recommendation = $("#model-library-recommendation");
    var note = $("#model-library-hero-note");
    var status = $("#model-library-hero-status");
    var dot = $("#model-library-status-dot");
    if (!recommendation || !note || !status || !dot) return;
    if (state.mode === "cloud") {
      recommendation.textContent = "云端模型";
      note.textContent = "本机不需要下载权重，音频会按云端配置发送处理。";
      status.textContent = state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured ? "云端已配置" : "等待云端配置";
      dot.dataset.status = state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured ? "ready" : "blocked";
      return;
    }
    var local = state.capabilities && state.capabilities.local;
    var recommendedId = local && local.recommended_model;
    var recommended = state.models.find(function (model) { return model.id === recommendedId; }) || selectedModel();
    if (!recommended) {
      recommendation.textContent = "正在读取推荐模型";
      note.textContent = "设备检测完成后，会在这里显示适合你的本地模型。";
      status.textContent = "检查中";
      dot.dataset.status = "idle";
      return;
    }
    recommendation.textContent = recommended.label;
    note.textContent = runtimeCopy(localRuntime()) + " · " + (recommended.best_for || recommended.languages || "适合课堂转录") + " · " + (recommended.size || "按需下载");
    status.textContent = modelManagerStatus(recommended, !modelRuntimeCompatible(recommended));
    dot.dataset.status = modelManagerStatusKind(recommended, !modelRuntimeCompatible(recommended));
  }

  function renderModelManagement() {
    var list = $("#model-management-list");
    var count = $("#model-management-count");
    if (!list || !count) return;
    if (!state.models.length) {
      count.textContent = "暂不可用";
      list.innerHTML = '<div class="model-management-empty">暂时无法读取模型目录，请确认后端已启动。</div>';
      return;
    }
    var readyCount = state.models.filter(function (model) { return model.status === "ready"; }).length;
    count.textContent = readyCount + " / " + state.models.length + " 已就绪";
    renderModelLibraryHero();
    list.innerHTML = state.models.map(function (model) {
      var selected = model.id === modelSelect.value;
      var runtimeMismatch = localRuntime() && !modelRuntimeCompatible(model);
      var statusKind = modelManagerStatusKind(model, runtimeMismatch);
      var statusClass = "is-status-" + statusKind;
      var action = "";
      if (model.status === "downloading") {
        action = '<button class="model-manager-action is-quiet" type="button" data-model-action="cancel" data-model-id="' + escapeHtml(model.id) + '">取消</button>';
      } else if (model.download_supported && model.status !== "ready" && !(model.status === "dependency_missing" && model.weights_available)) {
        action = '<button class="model-manager-action" type="button" data-model-action="download" data-model-id="' + escapeHtml(model.id) + '">' + (model.status === "failed" ? "重试" : "下载") + '</button>';
      } else if (model.status === "ready") {
        action = selected ? '<span class="model-manager-ready">当前使用</span>' : '<button class="model-manager-action is-outline" type="button" data-model-action="select" data-model-id="' + escapeHtml(model.id) + '">设为当前</button>';
      } else if (model.status === "runtime_download") {
        action = '<span class="model-manager-runtime">运行时管理</span>';
      } else if (runtimeMismatch) {
        action = '<span class="model-manager-runtime">当前设备不可用</span>';
      } else if (model.status === "dependency_missing") {
        action = '<span class="model-manager-runtime">需安装依赖</span>';
      }
      var progress = Math.max(0, Math.min(100, Number(model.progress) || 0));
      var progressMarkup = model.status === "downloading" ? '<div class="model-manager-progress" role="progressbar" aria-label="模型下载进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + progress + '"><span style="width:' + progress + '%"></span></div>' : "";
      return '<article class="model-manager-card ' + statusClass + (selected ? " is-selected" : "") + '">' +
        '<div class="model-manager-card-top"><div class="model-manager-status"><span class="model-manager-status-dot" data-status="' + statusKind + '" aria-hidden="true"></span><span>' + escapeHtml(modelManagerStatus(model, runtimeMismatch)) + '</span></div><div class="model-manager-actions">' + action + '</div></div>' +
        '<div class="model-manager-main"><div class="model-manager-title"><strong>' + escapeHtml(model.label) + '</strong><span class="model-runtime-tag">' + escapeHtml(modelRuntimeLabel(model.runtime)) + '</span>' + (selected ? '<span class="model-selected-tag">当前</span>' : '') + (model.id === (state.capabilities && state.capabilities.local && state.capabilities.local.recommended_model) ? '<span class="model-recommended-tag">推荐</span>' : '') + '</div>' +
        '<p>' + escapeHtml(model.best_for || model.languages || "通用转录") + '</p></div>' +
        '<dl class="model-manager-meta"><div><dt>速度</dt><dd>' + escapeHtml(model.speed || "—") + '</dd></div><div><dt>质量</dt><dd>' + escapeHtml(model.quality || "—") + '</dd></div><div><dt>占用</dt><dd>' + escapeHtml(model.resource || "—") + '</dd></div><div><dt>空间</dt><dd>' + escapeHtml(model.size || "—") + '</dd></div></dl>' +
        progressMarkup + '</article>';
    }).join("");
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
    var modelReady = state.mode === "cloud" ? Boolean(state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured) : localModelUsable(model);
    var audioReady = !state.saveAudio || state.audioReadiness === "ready";
    var basicReady = Boolean(connectionReady && modelReady && audioReady);
    $("#readiness-summary").textContent = basicReady ? "可以开始" : "还需准备";
    $("#readiness-summary").classList.toggle("is-ready", basicReady);
    $("#readiness-panel").setAttribute("aria-busy", state.phase === "checking" ? "true" : "false");
    syncQuickSettings();
  }

  function fetchModels() {
    return fetch("/api/models")
      .then(function (response) { if (!response.ok) throw new Error("models request failed"); return response.json(); })
      .then(function (data) {
        state.models = data.models || [];
        syncRuntimeModelSelection();
        renderModelManagement();
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
    return downloadModel(modelSelect.value);
  }

  function downloadModel(modelId) {
    var model = state.models.find(function (entry) { return entry.id === modelId; });
    if (!model || state.recording || state.starting) return;
    state.pendingModelId = model.id;
    var endpoint = model.status === "downloading" ? "/api/models/" + encodeURIComponent(model.id) + "/cancel" : "/api/models/" + encodeURIComponent(model.id) + "/download";
    fetch(endpoint, { method: "POST" })
      .then(function (response) {
        return response.json().then(function (data) { if (!response.ok && response.status !== 202) throw new Error(data.error || "模型操作失败"); return data; });
      })
      .then(function () { state.pendingModelId = null; return fetchModels(); })
      .catch(function (error) { showError("模型操作失败", error.message, "download-model"); });
  }

  function setupSettingsNavigation() {
    var navigationItems = $$("[data-settings-target]");
    var sections = $$("[data-settings-section]");
    var scrollArea = $("#settings-scroll");
    if (!navigationItems.length || !sections.length) return;
    var activate = function (targetId) {
      navigationItems.forEach(function (item) {
        var selected = item.dataset.settingsTarget === targetId;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-selected", selected ? "true" : "false");
      });
      sections.forEach(function (section) {
        section.hidden = section.id !== targetId;
      });
      if (scrollArea) scrollArea.scrollTop = 0;
    };
    navigationItems.forEach(function (item) {
      item.addEventListener("click", function () { activate(item.dataset.settingsTarget); });
    });
    activate("settings-session");
  }

  function setupSettingsDrawer() {
    if (!settingsDrawer) return;
    var openButton = $("#settings-button");
    var quickOpenButton = $("#quick-open-settings");
    var closeButton = $("#close-settings");
    var doneButton = $("#settings-done");
    var modelManagementList = $("#model-management-list");
    var close = function () {
      if (settingsDrawer.open) settingsDrawer.close();
      document.body.classList.remove("settings-open");
    };
    var open = function () {
      if (settingsDrawer.showModal) settingsDrawer.showModal();
      else settingsDrawer.setAttribute("open", "");
      document.body.classList.add("settings-open");
      if (closeButton) closeButton.focus();
    };
    if (openButton) openButton.addEventListener("click", open);
    if (quickOpenButton) quickOpenButton.addEventListener("click", open);
    if (closeButton) closeButton.addEventListener("click", close);
    if (doneButton) doneButton.addEventListener("click", close);
    settingsDrawer.addEventListener("cancel", close);
    settingsDrawer.addEventListener("close", function () { document.body.classList.remove("settings-open"); });
    settingsDrawer.addEventListener("click", function (event) { if (event.target === settingsDrawer) close(); });
    if (modelManagementList) modelManagementList.addEventListener("click", function (event) {
      var button = event.target.closest("[data-model-action]");
      if (!button) return;
      var modelId = button.dataset.modelId;
      if (button.dataset.modelAction === "download" || button.dataset.modelAction === "cancel") downloadModel(modelId);
      if (button.dataset.modelAction === "select") {
        modelSelect.value = modelId;
        savePreferences();
        renderModelManagement();
        renderReadiness();
        syncQuickSettings();
      }
    });
  }

  function segmentKey(segment) {
    return String(segment && (segment.id || segment.segment_id) || "");
  }

  function speakerLabel(segment) {
    var id = String(segment && (segment.speaker_id || segment.speakerId) || "").trim();
    if (!id) return "";
    var match = id.match(/(?:speaker|spk)[_-]?(\d+)/i);
    if (!match) return id;
    var number = Number(match[1]);
    if (!Number.isFinite(number) || number < 0) return id;
    return "说话人 " + (number < 26 ? String.fromCharCode(65 + number) : String(number + 1));
  }

  function normalizedSegmentText(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9\u3400-\u9fff]+/gi, "");
  }

  function sameLiveSegment(finalSegment, liveSegment) {
    if (!finalSegment || !liveSegment) return false;
    var finalText = normalizedSegmentText(finalSegment.text);
    var liveText = normalizedSegmentText(liveSegment.text);
    if (!finalText || !liveText) return false;
    var textMatches = finalText === liveText || finalText.indexOf(liveText) === 0 || liveText.indexOf(finalText) === 0;
    var finalStart = Number(finalSegment.start_ms);
    var liveStart = Number(liveSegment.start_ms);
    var timeMatches = Number.isFinite(finalStart) && Number.isFinite(liveStart) && Math.abs(finalStart - liveStart) <= 500;
    return textMatches || timeMatches;
  }

  function renderTranscriptArticle(article, segment, isFinal) {
    article.className = "transcript-segment" + (isFinal ? "" : " is-live-segment");
    article.dataset.segmentId = segmentKey(segment);
    article.setAttribute("aria-label", isFinal ? "已确认字幕" : "正在识别字幕");
    while (article.firstChild) article.removeChild(article.firstChild);
    var time = document.createElement("time");
    time.className = "segment-time";
    time.textContent = formatDuration(Math.max(0, Number(segment.start_ms || 0) / 1000));
    var content = document.createElement("div");
    content.className = "segment-content";
    var text = document.createElement("p");
    text.textContent = segment.text;
    var meta = document.createElement("small");
    var confidence = Number(segment.confidence);
    meta.textContent = isFinal
      ? Number.isFinite(confidence) && confidence > 0 ? "识别可信度 " + Math.round(confidence * 100) + "%" : "已确认字幕"
      : "正在识别 · 会自动更新";
    var speaker = speakerLabel(segment);
    if (speaker) {
      var speakerNode = document.createElement("span");
      speakerNode.className = "segment-speaker";
      speakerNode.textContent = speaker;
      meta.appendChild(speakerNode);
    }
    content.appendChild(text);
    content.appendChild(meta);
    article.appendChild(time);
    article.appendChild(content);
    if (isFinal) renderSegmentTranslation(article, segment);
  }

  function renderLiveSegment(segment) {
    var shouldFollow = $("#autoscroll-toggle").checked;
    if (!state.liveArticle || !state.liveArticle.parentNode) {
      state.liveArticle = document.createElement("article");
      feed.appendChild(state.liveArticle);
    }
    state.liveSegment = segment;
    renderTranscriptArticle(state.liveArticle, segment, false);
    if (shouldFollow) scrollToLatest();
  }

  function findFinalArticle(id) {
    if (!id) return null;
    var nodes = feed.querySelectorAll(".transcript-segment");
    for (var index = 0; index < nodes.length; index += 1) {
      // A draft row is not a confirmed caption, so an update must not target it.
      // Keep looking rather than stopping here: a confirmed row for the same id
      // can sit after the draft one.
      if (nodes[index] === state.liveArticle) continue;
      if (nodes[index].dataset.segmentId === String(id)) return nodes[index];
    }
    return null;
  }

  function removeSegment(id) {
    if (id === undefined || id === null) return;
    var key = String(id);
    var found = state.segments.some(function (segment) {
      return String(segment.id || segment.segment_id || "") === key;
    });
    if (!found) return;
    state.segments = state.segments.filter(function (segment) {
      return String(segment.id || segment.segment_id || "") !== key;
    });
    // The server retracted this caption because the merger folded it into a
    // neighbouring one. Leaving the row on screen would show the same sentence
    // twice, which is the duplicate the retraction exists to remove. Every row
    // carrying the id goes, because a draft row can share it with the confirmed
    // caption that replaced it.
    var nodes = feed.querySelectorAll(".transcript-segment");
    for (var index = 0; index < nodes.length; index += 1) {
      var article = nodes[index];
      if (article.dataset.segmentId !== key) continue;
      if (article === state.liveArticle) {
        state.liveArticle = null;
        state.liveSegment = null;
      }
      if (article.parentNode) article.parentNode.removeChild(article);
    }
    $("#segment-count").textContent = state.segments.length;
    scheduleSessionSave();
  }

  function addSegment(segment) {
    if (!segment || !segment.text) return;
    var isFinal = segment.is_final !== false;
    var currentDisplay = window.EchoTranscriptCurrent.resolve(
      currentText.textContent,
      segment,
      state.liveSegment
    );
    currentText.textContent = currentDisplay.text;
    currentSentence.classList.toggle("is-provisional", currentDisplay.provisional);
    emptyState.hidden = true;
    if (!isFinal) {
      renderLiveSegment(segment);
      return;
    }
    var shouldFollow = $("#autoscroll-toggle").checked;
    if (shouldFollow) protectFollowScroll();
    // The backend sometimes replaces a caption with a more complete decode of
    // the same audio. It keeps the id, so the existing line is updated in place
    // instead of adding a second one.
    var alreadyRendered = findFinalArticle(segment.id);
    if (alreadyRendered) {
      var stored = findSegment(segment.id);
      if (stored) {
        var changed = stored.text !== segment.text;
        stored.text = segment.text;
        stored.start_ms = segment.start_ms;
        stored.end_ms = segment.end_ms;
        if (changed) stored.translations = null;
        renderTranscriptArticle(alreadyRendered, stored, true);
        if (changed) enqueueTranslation(stored);
      } else {
        renderTranscriptArticle(alreadyRendered, segment, true);
      }
      scheduleSessionSave();
      return;
    }
    state.segments.push(segment);
    var article;
    if (state.liveArticle && sameLiveSegment(segment, state.liveSegment)) {
      var liveRemainder = window.EchoTranscriptCurrent.remainder(
        segment.text,
        state.liveSegment.text
      );
      if (liveRemainder) {
        article = document.createElement("article");
        renderTranscriptArticle(article, segment, true);
        feed.insertBefore(article, state.liveArticle);
        var previousLive = state.liveSegment;
        var remainderStart = Number(previousLive.start_ms) || 0;
        var finalEnd = Number(segment.end_ms);
        var liveEnd = Number(previousLive.end_ms);
        if (Number.isFinite(finalEnd)) {
          remainderStart = Math.max(remainderStart, finalEnd);
          if (Number.isFinite(liveEnd)) remainderStart = Math.min(remainderStart, liveEnd);
        }
        state.liveSegment = Object.assign({}, previousLive, {
          text: liveRemainder,
          start_ms: remainderStart,
          is_final: false
        });
        renderTranscriptArticle(state.liveArticle, state.liveSegment, false);
      } else {
        article = state.liveArticle;
        state.liveArticle = null;
        state.liveSegment = null;
        renderTranscriptArticle(article, segment, true);
      }
    } else {
      article = document.createElement("article");
      renderTranscriptArticle(article, segment, true);
      if (state.liveArticle && state.liveArticle.parentNode === feed) feed.insertBefore(article, state.liveArticle);
      else feed.appendChild(article);
    }
    $("#segment-count").textContent = state.segments.length;
    if (shouldFollow) {
      scrollToLatest();
      pulseFollowControl();
    }
    scheduleSessionSave();
    enqueueTranslation(segment);
  }

  function updateTranscriptSegment(segment) {
    if (!segment || !segmentKey(segment) || !segment.text) return;
    var id = segmentKey(segment);
    var stored = findSegment(id);
    if (!stored) {
      // A delayed diarization result can race the initial caption event during
      // startup. Treat it as a normal caption instead of losing the update.
      addSegment(segment);
      return;
    }
    Object.keys(segment).forEach(function (key) {
      if (key !== "translations") stored[key] = segment[key];
    });
    var article = findFinalArticle(id);
    if (article) {
      renderTranscriptArticle(article, stored, true);
    } else if (state.liveArticle && state.liveArticle.dataset.segmentId === id) {
      state.liveSegment = Object.assign({}, state.liveSegment || {}, segment);
      renderTranscriptArticle(state.liveArticle, state.liveSegment, false);
    }
    scheduleSessionSave();
  }

  function segmentTranslation(segment) {
    var translations = segment && segment.translations;
    return translations && translations[state.translationTarget] ? translations[state.translationTarget] : null;
  }

  function renderSegmentTranslation(article, segment) {
    var existing = article.querySelector(".translation-line");
    if (existing) existing.remove();
    var translation = segmentTranslation(segment);
    if (!translation) return;
    var line = document.createElement("p");
    line.className = "translation-line" + (translation.status === "pending" ? " is-pending" : translation.status === "failed" ? " is-failed" : "");
    line.textContent = translation.status === "pending" ? "翻译中…" : translation.status === "failed" ? "翻译失败 · 可重试" : translation.text || "";
    if (translation.status === "failed") {
      line.tabIndex = 0;
      line.setAttribute("role", "button");
      line.title = "点击重试翻译";
      line.addEventListener("click", function () { retryTranslation(segment); });
      line.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); retryTranslation(segment); } });
    }
    article.querySelector(".segment-content").insertBefore(line, article.querySelector(".segment-content").firstChild);
  }

  function findSegment(id) {
    return state.segments.find(function (segment) { return String(segment.id || segment.segment_id || "") === String(id); }) || null;
  }

  function applyTranslationResult(result) {
    (result && result.translations || []).forEach(function (item) {
      var segment = findSegment(item.segment_id || item.id);
      if (!segment) return;
      segment.translations = segment.translations || {};
      segment.translations[item.target_language || state.translationTarget] = item;
      $$(".transcript-segment").forEach(function (article) {
        if (article.dataset.segmentId === String(item.segment_id || item.id)) renderSegmentTranslation(article, segment);
      });
    });
    if (result && result.translations && result.translations.length) {
      setTranslationStatus((result.provider || "翻译服务") + "已完成", false);
      scheduleSessionSave();
    }
  }

  function setTranslationStatus(message, warning) {
    var node = $("#translation-status");
    if (!node) return;
    state.translationNotice = message;
    node.textContent = message;
    node.classList.toggle("is-warning", Boolean(warning));
  }

  function isFinalSegment(segment) {
    return Boolean(segment && segment.is_final !== false && segment.isFinal !== false);
  }

  function enqueueTranslation(segment) {
    if (!state.translationQueue || state.translationMode === "off" || !isFinalSegment(segment)) return false;
    var id = String(segment.id || segment.segment_id || "");
    var record = findSegment(id);
    if (record) {
      record.translations = record.translations || {};
      record.translations[state.translationTarget] = { status: "pending", mode: state.translationMode === "fast" ? "fast" : "model", provider: state.translationProvider, text: "" };
      $$(".transcript-segment").forEach(function (candidate) {
        if (candidate.dataset.segmentId === id) renderSegmentTranslation(candidate, record);
      });
    }
    state.translationQueue.enqueue(segment);
    return true;
  }

  function enqueueExistingTranslations() {
    if (!state.translationQueue || state.translationMode === "off") return 0;
    var queued = 0;
    state.segments.forEach(function (segment) {
      var existing = segmentTranslation(segment);
      if (existing && existing.status === "ready" && existing.text) return;
      if (enqueueTranslation(segment)) queued += 1;
    });
    return queued;
  }

  function retryTranslation(segment) {
    if (!state.translationQueue || !segment) return;
    setTranslationStatus("正在重试翻译…", false);
    state.translationQueue.retry([segment]);
  }

  function createTranslationQueue() {
    if (!window.EchoTranslationQueue) return;
    state.translationQueue = window.EchoTranslationQueue.create({
      // Translation only starts on confirmed captions, so it is already one
      // confirmation lag behind the speaker. Keep the batch small and the
      // debounce short so the Chinese line shows up while the sentence is
      // still on screen rather than half a minute later.
      batchSize: 2,
      debounceMs: 150,
      maxChars: 3000,
      send: function (items) {
        if (!state.socket || !state.connected) return Promise.reject(new Error("实时连接已断开"));
        return new Promise(function (resolve, reject) {
          state.socket.emit("translate_segments", {
            segment_ids: items.map(function (item) { return item.id; }),
            source_language: state.language,
            target_language: state.translationTarget,
            mode: state.translationMode === "fast" ? "fast" : state.translationMode === "precise" ? "model" : state.translationMode,
            provider: state.translationMode === "fast" ? state.translationProvider : state.translationModelMode,
            local_ready: translationCapability("local_model").configured
          }, function (result) {
            if (!result || result.status !== "success") {
              var error = new Error(result && result.error ? result.error.message : "翻译失败");
              error.code = result && result.error ? result.error.code : "TRANSLATION_FAILED";
              reject(error);
              return;
            }
            resolve(result);
          });
        });
      },
      onResult: applyTranslationResult,
      onError: function (error, items) {
        (items || []).forEach(function (item) {
          var segment = findSegment(item.id);
          if (!segment) return;
          segment.translations = segment.translations || {};
          segment.translations[state.translationTarget] = { status: "failed", mode: state.translationMode === "fast" ? "fast" : "model", provider: state.translationProvider, text: "", error: error.message || "翻译失败" };
          $$(".transcript-segment").forEach(function (article) { if (article.dataset.segmentId === item.id) renderSegmentTranslation(article, segment); });
        });
        var message = error && error.message ? error.message : "翻译失败";
        setTranslationStatus("翻译失败 · " + message, true);
        $("#feed-hint").textContent = "翻译失败，原文仍然可用：" + message;
        scheduleSessionSave();
      }
    });
  }

  function clearTranscript() {
    $$(".transcript-segment").forEach(function (node) { node.remove(); });
    state.segments = [];
    state.liveSegment = null;
    state.liveArticle = null;
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

  function clearPendingAudio() {
    state.pendingAudio = [];
    state.pendingAudioSeconds = 0;
  }

  function parseGlossaryInput(value) {
    return String(value || "").split(/[,;\n，、|]/).map(function (term) { return term.trim(); }).filter(function (term, index, all) {
      return term && all.indexOf(term) === index;
    }).slice(0, 50);
  }

  function captureNow() {
    return window.performance && typeof window.performance.now === "function" ? window.performance.now() : Date.now();
  }

  function captureOffsetMs() {
    if (!state.captureStartedAtMs) return null;
    return Math.max(0, Math.round(captureNow() - state.captureStartedAtMs));
  }

  function queuePendingAudio(buffer, sampleRate, offsetMs) {
    if (!buffer || !buffer.byteLength) return;
    var rate = Number(sampleRate) > 0 ? Number(sampleRate) : 16000;
    var durationSeconds = buffer.byteLength / 2 / rate;
    state.pendingAudio.push({ buffer: buffer, sampleRate: rate, durationSeconds: durationSeconds, offsetMs: offsetMs });
    state.pendingAudioSeconds += durationSeconds;
    var droppedSeconds = 0;
    while (state.pendingAudioSeconds > state.pendingAudioLimitSeconds && state.pendingAudio.length) {
      var dropped = state.pendingAudio.shift();
      state.pendingAudioSeconds -= dropped.durationSeconds;
      droppedSeconds += dropped.durationSeconds;
    }
    if (droppedSeconds > 0) {
      $("#feed-hint").textContent = "模型准备较慢，已跳过少量待转录音频；原声仍在保存";
    }
  }

  function emitAudioBuffer(buffer, sampleRate, offsetMs) {
    if (!state.recording || !state.socket || !state.connected || !state.sessionId || state.transcriptionFailed || !buffer || !buffer.byteLength) return false;
    var payload = { audio: encodeBase64(buffer), sample_rate: sampleRate || (state.context ? state.context.sampleRate : 16000), sequence: state.sequence++ };
    // Points the backend at this chunk's position on the recording clock, so
    // captions stay aligned with the saved audio even when audio was dropped.
    if (typeof offsetMs === "number" && isFinite(offsetMs) && offsetMs >= 0) payload.offset_ms = Math.round(offsetMs);
    state.socket.emit("audio_chunk", payload);
    return true;
  }

  function sendAudioBuffer(buffer) {
    if ((!state.recording && !state.starting) || !buffer || !buffer.byteLength) return;
    if (state.transcriptionFailed) return;
    var sampleRate = state.context ? state.context.sampleRate : 16000;
    var offsetMs = captureOffsetMs();
    state.audioBuffer.push(buffer, sampleRate, offsetMs).forEach(function (chunk) {
      if (!emitAudioBuffer(chunk.buffer, sampleRate, chunk.offsetMs)) queuePendingAudio(chunk.buffer, sampleRate, chunk.offsetMs);
    });
  }

  function flushPendingAudio() {
    if (!state.audioBuffer) return;
    var pending = state.pendingAudio.slice();
    clearPendingAudio();
    var tail = state.audioBuffer.flush();
    if (state.transcriptionFailed) {
      return;
    }
    if (!state.sessionId) {
      pending.forEach(function (item) { queuePendingAudio(item.buffer, item.sampleRate, item.offsetMs); });
      if (tail) queuePendingAudio(tail.buffer, state.context ? state.context.sampleRate : 16000, tail.offsetMs);
      return;
    }
    pending.forEach(function (item) { emitAudioBuffer(item.buffer, item.sampleRate, item.offsetMs); });
    if (tail) emitAudioBuffer(tail.buffer, state.context ? state.context.sampleRate : 16000, tail.offsetMs);
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

  function setQuickMicLabel(message) {
    var label = $("#quick-mic-level-label");
    if (label) label.textContent = message;
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
    syncQuickSettings();
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
    setQuickMicLabel("尚未测试");
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
      setQuickMicLabel("请说话或拍手测试…");
      function tick() {
        if (!state.micTestAnalyser) return;
        var level = updateMicLevel(analyserLevel(state.micTestAnalyser, data));
        if (level > 8) {
          state.micTestPassed = true;
          $("#mic-level-label").textContent = "输入正常 · 可以开始";
          setQuickMicLabel("输入正常 · 可以开始");
          $("#mic-readiness-status").textContent = "已检测到声音，可以开始";
        } else if (Date.now() - startedAt > 1500 && !state.micTestPassed) {
          $("#mic-level-label").textContent = "没有检测到声音";
          setQuickMicLabel("没有检测到声音");
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
      state.liveSegment = null;
      state.liveArticle = null;
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
      state.translationStopPromise = null;
      state.stopResult = null;
      state.transcriptionReady = false;
      state.transcriptionFailed = false;
      state.audioBuffer.reset();
      state.captureStartedAtMs = null;
      clearPendingAudio();
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
    if (!state.translationStopPromise) state.translationStopPromise = Promise.resolve();
    Promise.all([state.audioStopPromise, state.translationStopPromise]).then(function (values) {
      var audioManifest = values[0];
      return completeSession(state.stopResult, audioManifest);
    });
  }

  function readyToStart() {
    if (state.mode === "cloud") return Boolean(state.capabilities && state.capabilities.cloud && state.capabilities.cloud.configured);
    var model = selectedModel();
    return localModelUsable(model);
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
        outgoingMode = "cloud";
      } else {
        return showError("模型还没有准备好", model ? modelStatusText(model) : "请选择一个可用模型", "download-model", "MODEL_NOT_READY");
      }
    }
    if (state.saveAudio && !(await checkAudioReadiness())) return showError("原声保存不可用", "请关闭“保存原声”后仅保存字幕，或检查浏览器存储权限", "disable-audio", "AUDIO_STORAGE_UNAVAILABLE");
    if (!translationReady()) return showError("翻译服务未就绪", $("#translation-status").textContent, "", "TRANSLATION_NOT_CONFIGURED");
    state.starting = true;
    hideError();
    stopMicTest();
    setAppPhase("preparing", "正在准备麦克风", "请在浏览器提示中允许麦克风");
    try {
      state.stream = await navigator.mediaDevices.getUserMedia(microphoneConstraints());
      await refreshMicrophones();
      if (state.mode === "auto" && !localModelUsable(model)) outgoingMode = "cloud";
      var payload = { mode: outgoingMode, model: modelSelect.value, language: $("#language-select").value, sample_rate: 16000, enable_vad: true };
      // Course vocabulary steers the model before inference and repairs near-miss
      // spellings afterwards, so a lecturer's name stays spelled the same way.
      var glossaryTerms = parseGlossaryInput($("#glossary-input").value);
      if (glossaryTerms.length) payload.glossary = glossaryTerms;
      state.sequence = 0;
      state.audioBuffer.reset();
      clearPendingAudio();
      state.captureStartedAtMs = null;
      state.sessionStartedAt = Date.now();
      state.sessionId = null;
      state.transcriptionReady = false;
      state.transcriptionFailed = false;
      setAppPhase("preparing", "正在建立课堂连接", "模型会在后台启动，录音不会等待它");
      var completed = false;
      state.socket.emit("start_transcription", payload, async function (result) {
        if (completed) return;
        completed = true;
        state.starting = false;
        if (!result || ["starting", "success"].indexOf(result.status) === -1) {
          releaseCapture();
          return showError(result && result.error ? result.error.message : "无法开始转录", result && result.error ? result.error.action : "请切换推理路径后重试", null, result && result.error ? result.error.code : "START_FAILED");
        }
        state.sessionId = result.session_id;
        state.provider = result.provider;
        state.model = result.model || payload.model;
        state.language = payload.language;
        state.audioRecorder = null;
        state.audioManifest = null;
        if (state.translationQueue) state.translationQueue.start();
        $("#model-status").textContent = (result.provider || "provider") + (result.model ? " · " + result.model : "");
        try {
          setRecordingUi(true);
          await beginCapture();
          // One capture clock for both the transcript and the saved audio, so a
          // caption timestamp always points at the same moment in the recording.
          state.captureStartedAtMs = captureNow();
          flushPendingAudio();
          if (state.saveAudio) {
            state.audioRecorder = window.EchoAudioRecorder.create({
              stream: state.stream,
              sessionId: state.sessionId,
              repository: window.EchoAudioRepository,
              now: window.performance && typeof window.performance.now === "function" ? window.performance.now.bind(window.performance) : Date.now,
              startedAtMs: state.captureStartedAtMs
            });
            state.audioManifest = await state.audioRecorder.start();
          }
        } catch (captureError) {
          setRecordingUi(false);
          state.audioBuffer.reset();
          state.captureStartedAtMs = null;
          clearPendingAudio();
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
    state.translationStopPromise = state.translationQueue ? state.translationQueue.stop() : Promise.resolve();
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
        : "自动模式会优先使用设备适配的本地模型；本地不适合时会使用已配置云端。";
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
      syncQuickSettings();
    });
    checkAudioReadiness();
  }

  function setupTranslation() {
    [translationModeSelect, translationProviderSelect, translationTargetSelect, translationModelModeSelect].forEach(function (select) {
      select.addEventListener("change", function () {
        state.translationMode = translationModeSelect.value;
        state.translationProvider = translationProviderSelect.value;
        state.translationTarget = translationTargetSelect.value;
        state.translationModelMode = translationModelModeSelect.value;
        state.translationNotice = "";
        savePreferences();
        updateTranslationUi();
        renderReadiness();
        syncQuickSettings();
        if (state.recording && state.translationMode !== "off") {
          var queued = enqueueExistingTranslations();
          setTranslationStatus(queued ? "正在翻译已有字幕…" : "已开启 · 下一句字幕会自动翻译", false);
          syncQuickSettings();
        }
      });
    });
    createTranslationQueue();
    updateTranslationUi();
  }

  function setupModes() {
    $$(".mode-option").forEach(function (button) {
      button.addEventListener("click", function () {
        if (state.recording || state.starting) return;
        state.mode = button.dataset.mode;
        $$(".mode-option").forEach(function (option) { option.classList.toggle("is-selected", option === button); option.setAttribute("aria-pressed", option === button ? "true" : "false"); });
        syncRuntimeModelSelection();
      updatePrivacyCopy();
      savePreferences();
      renderReadiness();
      syncQuickSettings();
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
      syncQuickSettings();
    });
    $("#test-microphone").addEventListener("click", testMicrophone);
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) navigator.mediaDevices.addEventListener("devicechange", refreshMicrophones);
    refreshMicrophones().catch(function () { /* permission may be pending */ });
  }

  function setupQuickSettings() {
    if (!quickTranslationToggle) return;
    quickTranslationToggle.addEventListener("change", function () {
      if (quickTranslationToggle.checked) {
        if (translationModeSelect.value === "off") translationModeSelect.value = "fast";
      } else {
        translationModeSelect.value = "off";
      }
      translationModeSelect.dispatchEvent(new Event("change"));
    });
    syncQuickSettings();
  }

  function setupQuickRail() {
    var railBody = $("#quick-settings-body");
    if (!quickSettings || !quickRailToggle || !railBody) return;
    var saved = getUiPreferences();
    var setCollapsed = function (collapsed, persist) {
      document.body.classList.toggle("quick-settings-collapsed", collapsed);
      quickRailToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      quickRailToggle.setAttribute("aria-label", collapsed ? "展开右侧栏" : "收起右侧栏");
      quickRailToggle.title = collapsed ? "展开右侧栏" : "收起右侧栏";
      railBody.setAttribute("aria-hidden", collapsed ? "true" : "false");
      if (persist) saveUiPreferences({ quickRailCollapsed: collapsed });
    };
    quickRailToggle.addEventListener("click", function () {
      setCollapsed(!document.body.classList.contains("quick-settings-collapsed"), true);
    });
    setCollapsed(saved.quickRailCollapsed === true, false);
  }

  function setupLectureFocus() {
    var stage = $(".stage-panel");
    if (!stage || !feed || !lectureIntro || !lectureIntroToggle || !lectureIntroCompact || !lectureIntroCompactToggle) return;
    var saved = getUiPreferences();
    var userCollapsed = saved.lectureIntroCollapsed === true;
    var autoCollapsed = false;
    var setCollapsed = function (collapsed, persist) {
      document.body.classList.toggle("lecture-intro-collapsed", collapsed);
      stage.classList.toggle("is-header-collapsed", collapsed);
      lectureIntroCompact.hidden = !collapsed;
      lectureIntroCompact.setAttribute("aria-hidden", collapsed ? "false" : "true");
      lectureIntroToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      lectureIntroToggle.setAttribute("aria-label", collapsed ? "展开课堂介绍" : "收起课堂介绍");
      lectureIntroToggle.querySelector("#lecture-intro-toggle-label").textContent = collapsed ? "展开介绍" : "收起介绍";
      lectureIntroCompactToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      if (persist) saveUiPreferences({ lectureIntroCollapsed: collapsed });
    };
    var toggleManually = function () {
      userCollapsed = !stage.classList.contains("is-header-collapsed");
      autoCollapsed = false;
      setCollapsed(userCollapsed, true);
    };
    var focusForRecording = function () {
      if (!userCollapsed && !stage.classList.contains("is-header-collapsed")) {
        autoCollapsed = true;
        setCollapsed(true, false);
      }
    };
    lectureIntroToggle.addEventListener("click", toggleManually);
    lectureIntroCompactToggle.addEventListener("click", toggleManually);
    document.addEventListener("echonote:recording-started", focusForRecording);
    feed.addEventListener("scroll", function () {
      if (feed.scrollTop > 24 && !userCollapsed && !stage.classList.contains("is-header-collapsed")) {
        autoCollapsed = true;
        setCollapsed(true, false);
      } else if (feed.scrollTop <= 2 && autoCollapsed) {
        autoCollapsed = false;
        setCollapsed(false, false);
      }
    }, { passive: true });
    setCollapsed(userCollapsed, false);
  }

  function setupSocket() {
    if (!window.io) {
      setAppPhase("waiting", "正在等待实时连接", "实时连接问题会显示在课前检查");
      $("#feed-hint").textContent = "实时连接问题会显示在课前检查，点击开始时会再次尝试";
      renderReadiness();
      return;
    }
    state.socket = window.io({ transports: ["websocket", "polling"] });
    state.socket.on("connect", function () { state.connected = true; if (!state.recording && !state.starting) setAppPhase("idle", "实时连接已就绪", $("#model-status").textContent); renderReadiness(); });
    state.socket.on("disconnect", function () { state.connected = false; renderReadiness(); if (state.recording) showError("实时连接中断", "请先结束当前会话；网络恢复后可以重新开始", "retry-connection", "SOCKET_DISCONNECTED"); else setAppPhase("waiting", "正在等待实时连接", "实时连接问题会显示在课前检查"); });
    state.socket.on("transcription_session_created", function (result) {
      if (!result || !result.session_id || (state.sessionId && result.session_id !== state.sessionId)) return;
      state.sessionId = result.session_id;
      flushPendingAudio();
    });
    state.socket.on("transcription_ready", function (result) {
      if (!result || (result.session_id && state.sessionId && result.session_id !== state.sessionId)) return;
      state.transcriptionReady = true;
      state.provider = result.provider || state.provider;
      state.model = result.model || state.model;
      $("#model-status").textContent = (state.provider || "provider") + (state.model ? " · " + state.model : "");
      if (state.recording) setAppPhase("recording", "正在实时转录", $("#model-status").textContent);
      else syncQuickSettings();
    });
    state.socket.on("transcript_segment", addSegment);
    state.socket.on("transcript_segment_updated", updateTranscriptSegment);
    state.socket.on("transcript_segment_removed", function (payload) {
      removeSegment(payload && (payload.id || payload.segment_id));
    });
    state.socket.on("diarization_status", function (status) {
      if (!status || !state.recording) return;
      if (status.status === "ready") {
        $("#feed-hint").textContent = "说话人识别已就绪 · 原声仍保存在本机";
      } else if (status.status === "unavailable") {
        $("#feed-hint").textContent = "说话人识别暂不可用 · 转录仍会继续";
      }
    });
    state.socket.on("translation_result", applyTranslationResult);
    state.socket.on("translation_error", function (error) { setTranslationStatus("翻译失败 · 原文仍然可用", true); });
    state.socket.on("audio_backpressure", function (notice) {
      if (!state.recording) return;
      window.clearTimeout(state.backpressureTimer);
      $("#feed-hint").textContent = notice && notice.message ? notice.message : "本地处理较慢，已跳过少量音频，仍在继续转录";
      state.backpressureTimer = window.setTimeout(function () {
        if (state.recording) $("#feed-hint").textContent = "建议戴耳机，开启清晰输入";
      }, 5000);
    });
    state.socket.on("transcription_error", function (error) {
      state.transcriptionFailed = true;
      if (state.recording) {
        $("#feed-hint").textContent = "转录暂不可用 · 原声仍在保存，结束后可以复习录音";
      }
      showError(error.message || "转录出现问题", error.action, "", error.code);
    });
    state.socket.on("transcription_stopped", function (result) { if (state.recording || state.finishing) receiveStopResult(result); });
  }

  function setupHelp() {
    var dialog = $("#help-dialog");
    $("#help-button").addEventListener("click", function () { if (dialog.showModal) dialog.showModal(); });
    $("#close-help").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
  }

  function setupTranscriptFollow() {
    var toggle = $("#autoscroll-toggle");
    var pauseFollowForUserIntent = function (event) {
      if (state.followScrollLock || (event && event.isTrusted === false) || !toggle.checked) return;
      toggle.checked = false;
      updateFollowUi();
    };
    feed.addEventListener("scroll", function () {
      var atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 24;
      returnLatest.hidden = atBottom;
    });
    feed.addEventListener("wheel", function (event) { pauseFollowForUserIntent(event); }, { passive: true });
    feed.addEventListener("touchstart", function (event) { pauseFollowForUserIntent(event); }, { passive: true });
    feed.addEventListener("pointerdown", function (event) {
      if (event.target === feed) pauseFollowForUserIntent(event);
    }, { passive: true });
    feed.addEventListener("keydown", function (event) {
      if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].indexOf(event.key) !== -1) {
        pauseFollowForUserIntent(event);
      }
    });
    returnLatest.addEventListener("click", function () {
      toggle.checked = true;
      updateFollowUi();
      scrollToLatest();
      returnLatest.hidden = true;
      pulseFollowControl();
    });
    toggle.addEventListener("change", function () {
      updateFollowUi();
      if (this.checked) {
        scrollToLatest();
        returnLatest.hidden = true;
        pulseFollowControl();
      }
    });
    updateFollowUi();
  }

  $("#model-download-button").addEventListener("click", downloadSelectedModel);
  $("#model-select").addEventListener("change", function () { savePreferences(); renderReadiness(); renderModelManagement(); syncQuickSettings(); });
  $("#language-select").addEventListener("change", function () { savePreferences(); renderReadiness(); renderModelManagement(); syncQuickSettings(); });
  $("#course-title").addEventListener("input", savePreferences);
  $("#glossary-input").addEventListener("input", savePreferences);
  $("#error-action").addEventListener("click", function () {
    var action = this.dataset.action;
    if (action === "download-model") downloadModel(state.pendingModelId || modelSelect.value);
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
  setupTranslation();
  updatePrivacyCopy();
  setupMicrophone();
  setupQuickSettings();
  setupQuickRail();
  setupLectureFocus();
  setupSettingsNavigation();
  setupSettingsDrawer();
  setupHelp();
  setupTranscriptFollow();
  setupSocket();
  setAppPhase("checking", "正在检查听课条件", "马上告诉你是否可以开始");
  Promise.all([loadCapabilities(), fetchModels()]).then(function () {
    var waitingForConnection = !state.connected && state.phase === "waiting";
    setAppPhase(state.connected ? "idle" : waitingForConnection ? "waiting" : "checking", state.connected ? "实时连接已就绪" : "等待实时连接", $("#model-status").textContent);
    renderReadiness();
  });
  window.startLecture = startListening;
  window.stopLecture = stopListening;
  document.addEventListener("keydown", function (event) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (event.key === " " && tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA") { event.preventDefault(); startListening(); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#course-title").focus(); }
  });
})();
