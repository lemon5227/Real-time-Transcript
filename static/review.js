(function () {
  "use strict";

  var $ = function (selector) { return document.querySelector(selector); };
  var $$ = function (selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); };
  var state = { sessions: [], selected: null, filter: "all", query: "", audioUrl: null, audioBlob: null, audioManifest: null, audioSessionId: null, audioLoadToken: 0, transcriptionMode: "realtime", playingSegmentId: null, refinementBusy: false, refinementRun: 0, refinementPoll: null, translationBusy: false, translationRun: 0 };
  var list = $("#sessionList");
  var detailEmpty = $("#detailEmpty");
  var detailContent = $("#detailContent");
  var reviewAudio = $("#reviewAudio");

  function showNotice(kind, message, action) {
    var notice = $("#reviewNotice");
    notice.hidden = false;
    notice.className = "review-notice is-" + (kind || "info");
    notice.textContent = message + (action ? " · " + action : "");
  }

  function formatDuration(milliseconds) {
    var total = Math.max(0, Math.round(Number(milliseconds) || 0) / 1000);
    var minutes = Math.floor(total / 60).toString().padStart(2, "0");
    var seconds = Math.floor(total % 60).toString().padStart(2, "0");
    return minutes + ":" + seconds;
  }

  function formatDate(value) {
    if (!value) return "时间未知";
    try { return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value)); } catch (error) { return "时间未知"; }
  }

  function realtimeSegmentsOf(session) { return (session && session.segments) || []; }

  function refinedSegmentsOf(session) { return (session && (session.refinedSegments || session.refined_segments)) || []; }

  function refinementOf(session) { return (session && session.refinement) || {}; }

  function hasRefinedTranscript(session) { return refinedSegmentsOf(session).length > 0 && refinementOf(session).status === "ready"; }

  function segmentsOf(session) {
    if (session === state.selected && state.transcriptionMode === "refined" && hasRefinedTranscript(session)) return refinedSegmentsOf(session);
    return realtimeSegmentsOf(session);
  }

  function translationOf(segment, target) {
    var record = segment && segment.translations && segment.translations[target || $("#review-translation-target").value];
    return record || null;
  }

  function reviewTranslationSettings() {
    var mode = $("#review-translation-mode").value;
    return {
      mode: mode,
      provider: mode === "fast" ? $("#review-translation-provider").value : $("#review-translation-model").value,
      target: $("#review-translation-target").value
    };
  }

  function setReviewTranslationStatus(message, warning) {
    var node = $("#review-translation-status");
    node.textContent = message;
    node.classList.toggle("is-warning", Boolean(warning));
  }

  function updateTranslationControls() {
    var settings = reviewTranslationSettings();
    $("#review-translation-provider").disabled = settings.mode !== "fast" || state.translationBusy;
    $("#review-translation-model").disabled = settings.mode === "fast" || state.translationBusy;
    ["#translate-session", "#translate-selected", "#translate-failed"].forEach(function (selector) { $(selector).disabled = state.translationBusy || !state.selected; });
    $("#review-translation-progress").textContent = state.translationBusy ? "正在按批次翻译，已完成的段落会立即保存…" : "译文会保存在本机课堂笔记中；原声不会发送给翻译服务。";
  }

  function updateRefinementControls() {
    var session = state.selected;
    var refinement = refinementOf(session);
    var ready = hasRefinedTranscript(session);
    var button = $("#refine-transcription");
    var refinedButton = $("#transcript-mode-refined");
    var realtimeButton = $("#transcript-mode-realtime");
    if (!button || !refinedButton || !realtimeButton) return;
    button.disabled = state.refinementBusy || !session || !state.audioBlob;
    refinedButton.disabled = state.refinementBusy || !ready;
    realtimeButton.classList.toggle("is-selected", state.transcriptionMode !== "refined" || !ready);
    refinedButton.classList.toggle("is-selected", state.transcriptionMode === "refined" && ready);
    var status = $("#refine-transcription-status");
    var progress = $("#refine-transcription-progress");
    if (!session) {
      status.textContent = "选择课堂后开始";
      progress.textContent = "课后精细转录会使用保存的原声，不会覆盖实时稿。";
      return;
    }
    if (state.refinementBusy || refinement.status === "queued" || refinement.status === "processing") {
      status.textContent = "精细转录中";
      progress.textContent = "正在用完整上下文校正字幕；你仍可以切回实时稿。" + (refinement.progress ? " 已完成 " + refinement.progress + "%" : "");
    } else if (ready) {
      status.textContent = state.transcriptionMode === "refined" ? "正在查看精细稿" : "精细稿已就绪";
      progress.textContent = "精细稿已保留；实时稿仍在本机保存，可随时切换对照。";
      button.textContent = "重新精细转录";
    } else if (refinement.status === "failed") {
      status.textContent = "精细转录失败";
      status.classList.add("is-warning");
      progress.textContent = "没有覆盖实时稿。" + ((refinement.error && refinement.error.message) || "确认 MLX 模型可用后重试。");
      button.textContent = "重试精细转录";
    } else if (!state.audioBlob) {
      status.textContent = "需要保存原声";
      progress.textContent = "这节课没有可用原声；请在实时听课中开启原声保存后再试。";
    } else {
      status.textContent = "使用实时稿";
      progress.textContent = "使用已保存原声做一次完整上下文校正；不会覆盖实时稿。";
      button.textContent = "开始精细转录";
    }
    status.classList.toggle("is-warning", refinement.status === "failed" || !state.audioBlob);
  }

  function findReviewSegment(id) {
    return segmentsOf(state.selected).find(function (segment) { return String(segment.id || "") === String(id); }) || null;
  }

  function renderReviewTranslation(card, segment) {
    var oldLine = card.querySelector(".review-translation-line");
    if (oldLine) oldLine.remove();
    var settings = reviewTranslationSettings();
    var translation = translationOf(segment, settings.target);
    if (!translation || translation.status !== "ready" || !translation.text) return;
    var line = document.createElement("p");
    line.className = "review-translation-line";
    line.textContent = translation.text;
    var editor = card.querySelector(".segment-editor");
    if (editor && editor.nextSibling) card.insertBefore(line, editor.nextSibling);
    else card.appendChild(line);
  }

  function applyReviewTranslation(result) {
    var settings = reviewTranslationSettings();
    (result && result.translations || []).forEach(function (item) {
      var segment = findReviewSegment(item.segment_id || item.id);
      if (!segment) return;
      segment.translations = segment.translations || {};
      segment.translations[item.target_language || settings.target] = item;
      $$(".review-segment").forEach(function (card) {
        if (card.dataset.segmentId === String(item.segment_id || item.id)) renderReviewTranslation(card, segment);
      });
    });
  }

  function persistTranslationState() {
    if (!state.selected || !window.EchoStore) return Promise.resolve();
    return window.EchoStore.saveSession(state.selected).then(function () { renderLibrary(); }).catch(function () { showNotice("error", "译文已显示，但本地缓存失败", "请检查浏览器存储权限"); });
  }

  function translateSegments(candidates, options) {
    if (state.translationBusy || !state.selected) return Promise.resolve();
    var settings = reviewTranslationSettings();
    var force = Boolean(options && options.force);
    var target = settings.target;
    var pending = (candidates || []).filter(function (segment) {
      var translation = translationOf(segment, target);
      return force || !translation || translation.status !== "ready";
    });
    if (!pending.length) {
      setReviewTranslationStatus("已有译文缓存", false);
      $("#review-translation-progress").textContent = "所选段落已有译文，没有重复请求。";
      return Promise.resolve();
    }
    state.translationBusy = true;
    var run = state.translationRun + 1;
    state.translationRun = run;
    updateTranslationControls();
    setReviewTranslationStatus(settings.mode === "fast" ? "快速翻译中" : "模型翻译中", false);
    var total = pending.length;
    var completed = 0;
    var batches = [];
    for (var index = 0; index < pending.length; index += 20) batches.push(pending.slice(index, index + 20));
    return batches.reduce(function (chain, batch) {
      return chain.then(function () {
        if (run !== state.translationRun) return null;
        return fetch("/api/translate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: state.selected.id,
            segments: batch.map(function (segment) { return { id: segment.id, text: segment.text }; }),
            source_language: state.selected.language || "en",
            target_language: target,
            mode: settings.mode,
            provider: settings.provider
          })
        }).then(function (response) {
          return response.json().then(function (body) { if (!response.ok || body.status === "error") { var failure = new Error(body.error && body.error.message || "翻译失败"); failure.code = body.error && body.error.code; throw failure; } return body; });
        }).then(function (result) {
          applyReviewTranslation(result);
          completed += batch.length;
          $("#review-translation-progress").textContent = "已完成 " + completed + " / " + total + " 段 · 结果正在保存";
          return persistTranslationState();
        });
      });
    }, Promise.resolve()).then(function () {
      setReviewTranslationStatus("译文已保存", false);
      showNotice("success", "课后翻译已完成", "结果已保存在本机");
    }).catch(function (error) {
      setReviewTranslationStatus("翻译失败", true);
      $("#review-translation-progress").textContent = (error.message || "翻译失败") + " · 已完成的段落仍然保留，可重试";
      showNotice("error", "部分翻译未完成", "已完成的结果不会丢失");
    }).finally(function () {
      if (run === state.translationRun) { state.translationBusy = false; updateTranslationControls(); }
    });
  }

  function selectedReviewSegments() {
    var ids = $$(".segment-select:checked").map(function (input) { return input.value; });
    return segmentsOf(state.selected).filter(function (segment) { return ids.indexOf(String(segment.id || "")) !== -1; });
  }

  function failedReviewSegments() {
    var target = $("#review-translation-target").value;
    return segmentsOf(state.selected).filter(function (segment) { var translation = translationOf(segment, target); return translation && translation.status === "failed"; });
  }

  function formatBytes(bytes) {
    var value = Math.max(0, Number(bytes) || 0);
    if (value < 1024 * 1024) return Math.max(1, Math.round(value / 1024)) + "KB";
    return (value / (1024 * 1024)).toFixed(1) + "MB";
  }

  function releaseAudioUrl() {
    if (state.audioUrl) window.URL.revokeObjectURL(state.audioUrl);
    state.audioUrl = null;
    state.audioBlob = null;
    state.audioManifest = null;
    state.audioSessionId = null;
    reviewAudio.removeAttribute("src");
    reviewAudio.load();
    $("#export-audio").disabled = true;
    updateRefinementControls();
  }

  function setAudioStatus(status, meta) {
    $("#reviewAudioStatus").textContent = status;
    $("#reviewAudioMeta").textContent = meta;
  }

  function loadAudio(session) {
    if (session && state.audioSessionId === session.id) return Promise.resolve(state.audioBlob);
    var token = state.audioLoadToken + 1;
    state.audioLoadToken = token;
    releaseAudioUrl();
    state.audioSessionId = session && session.id;
    if (!session || !window.EchoAudioRepository) {
      setAudioStatus("没有保存原声", "这节课堂只有字幕记录");
      updateRefinementControls();
      return Promise.resolve(null);
    }
    setAudioStatus("正在读取原声", "正在从本机存储恢复音频…");
    return window.EchoAudioRepository.getManifest(session.id).then(function (manifest) {
      if (token !== state.audioLoadToken || !manifest || !manifest.chunkCount) {
        if (token === state.audioLoadToken) setAudioStatus("没有保存原声", "这节课堂只有字幕记录");
        return null;
      }
      state.audioManifest = manifest;
      return window.EchoAudioRepository.getPlayableBlob(session.id).then(function (blob) {
        if (token !== state.audioLoadToken) return null;
        if (!blob) {
          setAudioStatus("原声暂不可用", "本机没有找到可播放的音频片段");
          updateRefinementControls();
          return null;
        }
        state.audioBlob = blob;
        state.audioUrl = window.URL.createObjectURL(blob);
        reviewAudio.src = state.audioUrl;
        reviewAudio.load();
        $("#export-audio").disabled = false;
        setAudioStatus(manifest.status === "ready" ? "原声已保存" : "原声部分保存", formatBytes(manifest.bytes) + " · " + formatDuration(manifest.durationMs));
        updateRefinementControls();
        return blob;
      });
    }).catch(function () {
      if (token === state.audioLoadToken) { setAudioStatus("原声读取失败", "字幕仍然可以继续复习"); updateRefinementControls(); }
      return null;
    });
  }

  function refinementErrorMessage(body, fallback) {
    return body && body.error && body.error.message ? body.error.message : (fallback || "精细转录失败");
  }

  function setRefinementFailed(message) {
    if (!state.selected) return;
    state.refinementBusy = false;
    state.selected.refinement = Object.assign({}, refinementOf(state.selected), {
      status: "failed",
      error: { message: message || "精细转录失败" },
      completedAt: new Date().toISOString()
    });
    updateRefinementControls();
    saveSelected().then(function () { renderDetail(); showNotice("error", "精细转录未完成", message || "确认 MLX 模型可用后重试"); });
  }

  function pollRefinement(jobId, run) {
    if (run !== state.refinementRun || !state.selected) return;
    fetch("/api/refine-transcription/" + encodeURIComponent(jobId)).then(function (response) {
      return response.json().then(function (body) { if (!response.ok || body.status === "error") throw new Error(refinementErrorMessage(body, "精细转录任务读取失败")); return body; });
    }).then(function (job) {
      if (run !== state.refinementRun || !state.selected) return;
      state.selected.refinement = Object.assign({}, refinementOf(state.selected), {
        status: job.status,
        provider: "mlx",
        model: job.model || refinementOf(state.selected).model,
        progress: Number(job.progress) || 0,
        startedAt: job.startedAt || refinementOf(state.selected).startedAt,
        error: job.error || null
      });
      if (job.status === "ready") {
        state.selected.refinedSegments = (job.segments || []).map(function (segment, index) {
          return { id: segment.id || "refined-" + index, text: String(segment.text || "").trim(), startMs: Number(segment.startMs) || 0, endMs: Number(segment.endMs) || 0, isFinal: true, note: "", starred: false, translations: {} };
        });
        state.selected.refinement.completedAt = job.completedAt || new Date().toISOString();
        state.refinementBusy = false;
        state.transcriptionMode = "refined";
        renderDetail();
        saveSelected().then(function () { showNotice("success", "精细转录完成", "实时稿仍然保留，可切换对照"); });
        return;
      }
      if (job.status === "failed") {
        setRefinementFailed(refinementErrorMessage(job, "确认 MLX 模型可用后重试"));
        return;
      }
      updateRefinementControls();
      state.refinementPoll = window.setTimeout(function () { pollRefinement(jobId, run); }, 700);
    }).catch(function (error) {
      if (run === state.refinementRun) setRefinementFailed(error.message || "精细转录任务读取失败");
    });
  }

  function startFineTranscription() {
    if (state.refinementBusy || !state.selected) return;
    if (!state.audioBlob) return showNotice("info", "这节课堂没有可用原声", "请先保存原声后再做精细转录");
    var run = state.refinementRun + 1;
    state.refinementRun = run;
    state.refinementBusy = true;
    state.selected.refinement = {
      status: "queued",
      provider: "mlx",
      model: state.selected.model || "mlx-community/parakeet-tdt-0.6b-v3",
      progress: 0,
      startedAt: new Date().toISOString(),
      completedAt: null,
      error: null
    };
    updateRefinementControls();
    var form = new FormData();
    form.append("audio", state.audioBlob, (state.audioManifest && state.audioManifest.extension ? "lecture." + state.audioManifest.extension : "lecture.webm"));
    form.append("language", state.selected.language || "en");
    form.append("model", "mlx-community/parakeet-tdt-0.6b-v3");
    fetch("/api/refine-transcription", { method: "POST", body: form }).then(function (response) {
      return response.json().then(function (body) { if (!response.ok || body.status === "error") throw new Error(refinementErrorMessage(body, "精细转录无法开始")); return body; });
    }).then(function (job) {
      if (run !== state.refinementRun) return;
      pollRefinement(job.job_id, run);
    }).catch(function (error) {
      if (run === state.refinementRun) setRefinementFailed(error.message || "精细转录无法开始");
    });
  }

  function seekToSegment(segment) {
    if (!state.audioUrl || !segment) return;
    reviewAudio.currentTime = Math.max(0, Number(segment.startMs) || 0) / 1000;
    reviewAudio.play().catch(function () {});
  }

  function syncPlayingSegment() {
    var currentMs = (Number(reviewAudio.currentTime) || 0) * 1000;
    var segments = segmentsOf(state.selected);
    var activeIndex = -1;
    segments.some(function (segment, index) {
      var next = segments[index + 1];
      var endMs = Number(segment && segment.endMs) || (next ? Number(next.startMs) || currentMs : Number.POSITIVE_INFINITY);
      if (segment && Number(segment.startMs) <= currentMs && currentMs < endMs) {
        activeIndex = index;
        return true;
      }
      return false;
    });
    var activeSegment = activeIndex >= 0 ? segments[activeIndex] : null;
    var activeId = activeSegment ? String(activeSegment.id || "") : null;
    var changed = activeId !== state.playingSegmentId;
    state.playingSegmentId = activeId;
    var stream = $("#reviewStream");
    $$(".review-segment").forEach(function (card, index) {
      var isPlaying = index === activeIndex;
      card.classList.toggle("is-playing", isPlaying);
      if (!isPlaying || !changed || !stream) return;
      var cardRect = card.getBoundingClientRect();
      var streamRect = stream.getBoundingClientRect();
      var outsideViewport = cardRect.bottom < streamRect.top || cardRect.top > streamRect.bottom;
      if (outsideViewport && typeof card.scrollIntoView === "function") {
        var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        card.scrollIntoView({ block: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
      }
    });
  }

  function downloadAudio() {
    if (!state.audioUrl || !state.selected || !state.audioManifest) return showNotice("info", "这节课堂没有可导出的原声");
    var link = document.createElement("a");
    link.href = state.audioUrl;
    link.download = (state.selected.title || "lecture").replace(/[\\/:*?"<>|]+/g, "-") + "." + (state.audioManifest.extension || "webm");
    link.click();
    showNotice("success", "已准备导出原声", (state.audioManifest.extension || "webm").toUpperCase());
  }

  function matchesSession(session) {
    var query = state.query.trim().toLowerCase();
    var segments = segmentsOf(session);
    if (state.filter === "starred" && !segments.some(function (segment) { return segment.starred; })) return false;
    if (state.filter === "notes" && !segments.some(function (segment) { return segment.note; })) return false;
    if (!query) return true;
    var haystack = [session.title, session.language, session.provider].concat(segments.map(function (segment) { return segment.text + " " + segment.note; })).join(" ").toLowerCase();
    return haystack.indexOf(query) !== -1;
  }

  function saveSelected(message) {
    if (!state.selected) return Promise.resolve();
    return window.EchoStore.saveSession(state.selected).then(function () {
      renderLibrary();
      if (message) showNotice("success", message);
    }).catch(function () { showNotice("error", "本次编辑未能保存", "请检查浏览器的本地存储权限"); });
  }

  function renderLibrary() {
    var visible = state.sessions.filter(matchesSession);
    $("#session-count").textContent = state.sessions.length;
    list.textContent = "";
    if (!visible.length) {
      var empty = document.createElement("div");
      empty.className = "list-empty";
      var icon = document.createElement("span"); icon.textContent = state.sessions.length ? "⌕" : "◌";
      var title = document.createElement("p"); title.textContent = state.sessions.length ? "没有匹配的课堂" : "还没有课堂笔记";
      var hint = document.createElement("small"); hint.textContent = state.sessions.length ? "换个关键词或清除筛选试试。" : "完成一次听课后，它们会出现在这里。";
      empty.append(icon, title, hint); list.appendChild(empty); return;
    }
    visible.forEach(function (session) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "session-item" + (state.selected && state.selected.id === session.id ? " is-selected" : "");
      item.addEventListener("click", function () { selectSession(session.id); });
      var top = document.createElement("div"); top.className = "session-item-top";
      var title = document.createElement("strong"); title.textContent = session.title || "未命名课堂";
      var starCount = segmentsOf(session).filter(function (segment) { return segment.starred; }).length;
      var star = document.createElement("span"); star.className = "session-star"; star.textContent = starCount ? "★ " + starCount : "";
      top.append(title, star);
      var meta = document.createElement("div"); meta.className = "session-item-meta"; meta.textContent = formatDate(session.createdAt) + "  ·  " + formatDuration(session.durationMs);
      var preview = document.createElement("p"); preview.textContent = segmentsOf(session)[0] ? segmentsOf(session)[0].text : "空课堂记录";
      item.append(top, meta, preview); list.appendChild(item);
    });
  }

  function selectSession(id) {
    var session = state.sessions.find(function (candidate) { return candidate.id === id; });
    state.refinementRun += 1;
    state.refinementBusy = false;
    if (state.refinementPoll) window.clearTimeout(state.refinementPoll);
    state.refinementPoll = null;
    state.selected = session || null;
    state.playingSegmentId = null;
    state.transcriptionMode = hasRefinedTranscript(session) ? "refined" : "realtime";
    renderLibrary();
    renderDetail();
  }

  function renderDetail() {
    if (!state.selected) { detailEmpty.hidden = false; detailContent.hidden = true; updateTranslationControls(); updateRefinementControls(); return; }
    detailEmpty.hidden = true;
    detailContent.hidden = false;
    $("#review-title").value = state.selected.title || "未命名课堂";
    $("#review-meta").textContent = formatDate(state.selected.createdAt) + "  ·  " + (state.selected.provider || "unknown") + (state.selected.model ? " / " + state.selected.model : "") + "  ·  " + (state.selected.language || "en");
    $("#review-segment-count").textContent = segmentsOf(state.selected).length;
    $("#review-duration").textContent = formatDuration(state.selected.durationMs);
    loadAudio(state.selected);
    $("#review-transcript-source").textContent = state.transcriptionMode === "refined" && hasRefinedTranscript(state.selected) ? "精细稿 · 完整上下文" : "实时稿 · 上课同步记录";
    var stream = $("#reviewStream"); stream.textContent = "";
    segmentsOf(state.selected).forEach(function (segment, index) {
      var card = document.createElement("article"); card.className = "review-segment" + (segment.starred ? " is-starred" : ""); card.dataset.segmentIndex = String(index); card.dataset.segmentId = String(segment.id || "");
      var header = document.createElement("div"); header.className = "review-segment-header";
      var time = document.createElement("time"); time.textContent = window.EchoExport.timestamp(segment.startMs, "."); time.tabIndex = 0; time.setAttribute("role", "button"); time.setAttribute("aria-label", "跳转到第 " + (index + 1) + " 段字幕"); time.addEventListener("click", function () { seekToSegment(segment); }); time.addEventListener("keydown", function (event) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); seekToSegment(segment); } });
      var selectLabel = document.createElement("label"); selectLabel.className = "segment-select-label"; var selectInput = document.createElement("input"); selectInput.type = "checkbox"; selectInput.className = "segment-select"; selectInput.value = String(segment.id || ""); selectInput.setAttribute("aria-label", "选择第 " + (index + 1) + " 段字幕"); var selectText = document.createElement("span"); selectText.textContent = "选择"; selectLabel.append(selectInput, selectText);
      var starButton = document.createElement("button"); starButton.type = "button"; starButton.className = "star-toggle"; starButton.dataset.starred = String(Boolean(segment.starred)); starButton.setAttribute("aria-label", segment.starred ? "取消重点标记" : "标记为重点"); starButton.textContent = segment.starred ? "★ 重点" : "☆ 标记重点";
      starButton.addEventListener("click", function () { segment.starred = !segment.starred; renderDetail(); saveSelected(); });
      var translateButton = document.createElement("button"); translateButton.type = "button"; translateButton.className = "translate-segment"; translateButton.textContent = "译本句"; translateButton.addEventListener("click", function () { translateSegments([segment], { force: true }); });
      header.append(time, selectLabel, translateButton, starButton);
      var editor = document.createElement("textarea"); editor.className = "segment-editor"; editor.rows = 2; editor.value = segment.text || ""; editor.setAttribute("aria-label", "编辑第 " + (index + 1) + " 段字幕");
      editor.addEventListener("input", function () { segment.text = editor.value; window.clearTimeout(editor._saveTimer); editor._saveTimer = window.setTimeout(function () { saveSelected("已保存修改"); }, 500); });
      card.append(header, editor);
      renderReviewTranslation(card, segment);
      var noteRow = document.createElement("label"); noteRow.className = "note-row"; var noteLabel = document.createElement("span"); noteLabel.textContent = "NOTE"; var noteInput = document.createElement("textarea"); noteInput.id = index === 0 ? "noteInput" : "noteInput-" + index; noteInput.rows = 1; noteInput.placeholder = "补充你的理解、例子或待查概念…"; noteInput.value = segment.note || ""; noteInput.setAttribute("aria-label", "为第 " + (index + 1) + " 段字幕添加笔记"); noteInput.addEventListener("input", function () { segment.note = noteInput.value; window.clearTimeout(noteInput._saveTimer); noteInput._saveTimer = window.setTimeout(function () { saveSelected("已保存笔记"); }, 500); }); noteRow.append(noteLabel, noteInput);
      card.append(noteRow); stream.appendChild(card);
    });
    updateTranslationControls();
    updateRefinementControls();
  }

  function chooseTranscriptMode(mode) {
    if (!state.selected) return;
    if (mode === "refined" && !hasRefinedTranscript(state.selected)) {
      return showNotice("info", "精细稿还没有准备好", "先点击开始精细转录");
    }
    state.transcriptionMode = mode === "refined" ? "refined" : "realtime";
    renderDetail();
  }

  function download(format) {
    if (!state.selected) return showNotice("info", "请先选择一节课堂");
    var formatters = { txt: window.EchoExport.formatPlainText, md: window.EchoExport.formatMarkdown, vtt: window.EchoExport.formatVtt, srt: window.EchoExport.formatSrt };
    var options = { translationMode: $("#export-translation-mode").value, targetLanguage: $("#review-translation-target").value, segments: segmentsOf(state.selected) };
    var content = format === "vtt" || format === "srt" ? formatters[format](segmentsOf(state.selected), options) : formatters[format](state.selected, options);
    var mime = format === "md" ? "text/markdown;charset=utf-8" : format === "vtt" || format === "srt" ? "text/plain;charset=utf-8" : "text/plain;charset=utf-8";
    var link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([content], { type: mime })); link.download = (state.selected.title || "lecture").replace(/[\\/:*?"<>|]+/g, "-") + "." + format; link.click(); window.setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
    showNotice("success", "已准备导出文件", format.toUpperCase());
  }

  function loadLibrary() {
    window.EchoStore.listSessions().then(function (sessions) { state.sessions = sessions || []; renderLibrary(); }).catch(function () { showNotice("error", "无法读取本地课堂笔记", "请检查浏览器存储权限"); renderLibrary(); });
  }

  $("#searchInput").addEventListener("input", function (event) { state.query = event.target.value; renderLibrary(); });
  $$(".filter-tab").forEach(function (button) { button.addEventListener("click", function () { state.filter = button.dataset.filter; $$(".filter-tab").forEach(function (tab) { tab.classList.toggle("is-selected", tab === button); }); renderLibrary(); }); });
  $("#review-title").addEventListener("change", function (event) { if (state.selected) { state.selected.title = event.target.value.trim() || "未命名课堂"; saveSelected("已保存标题"); } });
  $("#delete-session").addEventListener("click", function () { if (!state.selected || !window.confirm("确定删除这节课堂记录吗？此操作无法撤销。")) return; var id = state.selected.id; var removeAudio = window.EchoAudioRepository && window.EchoAudioRepository.deleteRecording ? window.EchoAudioRepository.deleteRecording(id) : Promise.resolve(); removeAudio.then(function () { return window.EchoStore.deleteSession(id); }).then(function () { state.sessions = state.sessions.filter(function (session) { return session.id !== id; }); state.selected = null; releaseAudioUrl(); renderLibrary(); renderDetail(); showNotice("success", "课堂记录已删除"); }).catch(function () { showNotice("error", "删除失败", "课堂记录和原声都未删除"); }); });
  $$(".export-button").forEach(function (button) { button.addEventListener("click", function () { download(button.dataset.format); }); });
  $("#export-audio").addEventListener("click", downloadAudio);
  $("#refine-transcription").addEventListener("click", startFineTranscription);
  $("#transcript-mode-realtime").addEventListener("click", function () { chooseTranscriptMode("realtime"); });
  $("#transcript-mode-refined").addEventListener("click", function () { chooseTranscriptMode("refined"); });
  $("#translate-session").addEventListener("click", function () { translateSegments(segmentsOf(state.selected)); });
  $("#translate-selected").addEventListener("click", function () { var segments = selectedReviewSegments(); if (!segments.length) return showNotice("info", "请先勾选要翻译的段落"); translateSegments(segments); });
  $("#translate-failed").addEventListener("click", function () { var segments = failedReviewSegments(); if (!segments.length) return showNotice("info", "当前没有失败的译文"); translateSegments(segments, { force: true }); });
  ["#review-translation-mode", "#review-translation-provider", "#review-translation-model", "#review-translation-target"].forEach(function (selector) { $(selector).addEventListener("change", function () { updateTranslationControls(); renderDetail(); }); });
  reviewAudio.addEventListener("timeupdate", syncPlayingSegment);
  window.addEventListener("beforeunload", releaseAudioUrl);
  document.addEventListener("keydown", function (event) { if (event.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") { event.preventDefault(); $("#searchInput").focus(); } });
  loadLibrary();
}());
