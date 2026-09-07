(function () {
  "use strict";

  var $ = function (selector) { return document.querySelector(selector); };
  var $$ = function (selector) { return Array.prototype.slice.call(document.querySelectorAll(selector)); };
  var state = { sessions: [], selected: null, filter: "all", query: "" };
  var list = $("#sessionList");
  var detailEmpty = $("#detailEmpty");
  var detailContent = $("#detailContent");

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

  function segmentsOf(session) { return (session && session.segments) || []; }

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
    state.selected = session || null;
    renderLibrary();
    renderDetail();
  }

  function renderDetail() {
    if (!state.selected) { detailEmpty.hidden = false; detailContent.hidden = true; return; }
    detailEmpty.hidden = true;
    detailContent.hidden = false;
    $("#review-title").value = state.selected.title || "未命名课堂";
    $("#review-meta").textContent = formatDate(state.selected.createdAt) + "  ·  " + (state.selected.provider || "unknown") + (state.selected.model ? " / " + state.selected.model : "") + "  ·  " + (state.selected.language || "en");
    $("#review-segment-count").textContent = segmentsOf(state.selected).length;
    $("#review-duration").textContent = formatDuration(state.selected.durationMs);
    var stream = $("#reviewStream"); stream.textContent = "";
    segmentsOf(state.selected).forEach(function (segment, index) {
      var card = document.createElement("article"); card.className = "review-segment" + (segment.starred ? " is-starred" : "");
      var header = document.createElement("div"); header.className = "review-segment-header";
      var time = document.createElement("time"); time.textContent = window.EchoExport.timestamp(segment.startMs, ".");
      var starButton = document.createElement("button"); starButton.type = "button"; starButton.className = "star-toggle"; starButton.dataset.starred = String(Boolean(segment.starred)); starButton.setAttribute("aria-label", segment.starred ? "取消重点标记" : "标记为重点"); starButton.textContent = segment.starred ? "★ 重点" : "☆ 标记重点";
      starButton.addEventListener("click", function () { segment.starred = !segment.starred; renderDetail(); saveSelected(); });
      header.append(time, starButton);
      var editor = document.createElement("textarea"); editor.className = "segment-editor"; editor.rows = 2; editor.value = segment.text || ""; editor.setAttribute("aria-label", "编辑第 " + (index + 1) + " 段字幕");
      editor.addEventListener("input", function () { segment.text = editor.value; window.clearTimeout(editor._saveTimer); editor._saveTimer = window.setTimeout(function () { saveSelected("已保存修改"); }, 500); });
      var noteRow = document.createElement("label"); noteRow.className = "note-row"; var noteLabel = document.createElement("span"); noteLabel.textContent = "NOTE"; var noteInput = document.createElement("textarea"); noteInput.id = index === 0 ? "noteInput" : "noteInput-" + index; noteInput.rows = 1; noteInput.placeholder = "补充你的理解、例子或待查概念…"; noteInput.value = segment.note || ""; noteInput.setAttribute("aria-label", "为第 " + (index + 1) + " 段字幕添加笔记"); noteInput.addEventListener("input", function () { segment.note = noteInput.value; window.clearTimeout(noteInput._saveTimer); noteInput._saveTimer = window.setTimeout(function () { saveSelected("已保存笔记"); }, 500); }); noteRow.append(noteLabel, noteInput);
      card.append(header, editor, noteRow); stream.appendChild(card);
    });
  }

  function download(format) {
    if (!state.selected) return showNotice("info", "请先选择一节课堂");
    var formatters = { txt: window.EchoExport.formatPlainText, md: window.EchoExport.formatMarkdown, vtt: window.EchoExport.formatVtt, srt: window.EchoExport.formatSrt };
    var content = formatters[format](state.selected);
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
  $("#delete-session").addEventListener("click", function () { if (!state.selected || !window.confirm("确定删除这节课堂记录吗？此操作无法撤销。")) return; var id = state.selected.id; window.EchoStore.deleteSession(id).then(function () { state.sessions = state.sessions.filter(function (session) { return session.id !== id; }); state.selected = null; renderLibrary(); renderDetail(); showNotice("success", "课堂记录已删除"); }).catch(function () { showNotice("error", "删除失败", "请重试"); }); });
  $$(".export-button").forEach(function (button) { button.addEventListener("click", function () { download(button.dataset.format); }); });
  document.addEventListener("keydown", function (event) { if (event.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") { event.preventDefault(); $("#searchInput").focus(); } });
  loadLibrary();
}());
