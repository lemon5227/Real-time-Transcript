(function (root) {
  "use strict";

  function value(segment, camel, snake, fallback) {
    var result = segment && segment[camel] !== undefined ? segment[camel] : segment && segment[snake];
    return result === undefined || result === null ? fallback : result;
  }

  function timestamp(milliseconds, separator) {
    var total = Math.max(0, Math.round(Number(milliseconds) || 0));
    var hours = Math.floor(total / 3600000);
    var minutes = Math.floor((total % 3600000) / 60000);
    var seconds = Math.floor((total % 60000) / 1000);
    var millis = total % 1000;
    return [hours, minutes, seconds].map(function (part) { return String(part).padStart(2, "0"); }).join(":") + separator + String(millis).padStart(3, "0");
  }

  function segmentTimestamp(segment, key, separator) {
    return timestamp(value(segment, key, key.replace(/[A-Z]/g, function (letter) { return "_" + letter.toLowerCase(); }), 0), separator);
  }

  function formatVtt(segments) {
    var cues = (segments || []).map(function (segment, index) {
      return String(index + 1) + "\n" + segmentTimestamp(segment, "startMs", ".") + " --> " + segmentTimestamp(segment, "endMs", ".") + "\n" + String(value(segment, "text", "text", "")).trim();
    });
    return "WEBVTT\n\n" + cues.join("\n\n") + (cues.length ? "\n" : "");
  }

  function formatSrt(segments) {
    var cues = (segments || []).map(function (segment, index) {
      return String(index + 1) + "\n" + segmentTimestamp(segment, "startMs", ",") + " --> " + segmentTimestamp(segment, "endMs", ",") + "\n" + String(value(segment, "text", "text", "")).trim();
    });
    return cues.join("\n\n") + (cues.length ? "\n" : "");
  }

  function formatMarkdown(session) {
    var record = session || {};
    var segments = record.segments || [];
    var lines = ["# " + (record.title || "未命名课堂"), "", "- 日期：" + (record.createdAt || record.created_at || "未记录"), "- 语言：" + (record.language || "未指定"), "- Provider：" + (record.provider || "未记录"), "", "## 字幕", ""];
    segments.forEach(function (segment) {
      var marker = segment.starred ? " ★" : "";
      var note = segment.note ? "\n\n> 笔记：" + segment.note : "";
      lines.push("**[" + segmentTimestamp(segment, "startMs", ".") + " → " + segmentTimestamp(segment, "endMs", ".") + "]**" + marker, "", String(segment.text || "").trim() + note, "");
    });
    return lines.join("\n");
  }

  function formatPlainText(session) {
    var record = session || {};
    var lines = [(record.title || "未命名课堂"), "=".repeat(Math.max(8, (record.title || "未命名课堂").length)), ""];
    (record.segments || []).forEach(function (segment) {
      var note = segment.note ? "\n  笔记：" + segment.note : "";
      var marker = segment.starred ? " ★" : "";
      lines.push("[" + segmentTimestamp(segment, "startMs", ".") + "]" + marker + " " + String(segment.text || "").trim() + note);
    });
    return lines.join("\n") + "\n";
  }

  root.EchoExport = { formatVtt: formatVtt, formatSrt: formatSrt, formatMarkdown: formatMarkdown, formatPlainText: formatPlainText, timestamp: timestamp };
  root.formatVtt = formatVtt;
  root.formatSrt = formatSrt;
  root.formatMarkdown = formatMarkdown;
  root.formatPlainText = formatPlainText;
}(window));
