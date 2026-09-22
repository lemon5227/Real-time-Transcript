(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.EchoTranscriptCurrent = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function normalize(text) {
    return String(text || "").toLowerCase().replace(/[^a-z0-9\u3400-\u9fff]/gi, "");
  }

  function remainder(finalText, liveText) {
    var finalNormalized = normalize(finalText);
    var liveNormalized = normalize(liveText);
    if (
      !finalNormalized
      || liveNormalized.indexOf(finalNormalized) !== 0
      || finalNormalized.length >= liveNormalized.length
    ) {
      return null;
    }

    var matched = 0;
    var endIndex = -1;
    for (var index = 0; index < liveText.length; index += 1) {
      if (/[a-z0-9\u3400-\u9fff]/i.test(liveText.charAt(index))) matched += 1;
      if (matched === finalNormalized.length) {
        endIndex = index + 1;
        break;
      }
    }
    if (endIndex < 0 || /[a-z0-9\u3400-\u9fff]/i.test(liveText.charAt(endIndex) || "")) return null;

    var rest = liveText.slice(endIndex).replace(/^[\s.,!?;:，。！？；：、—-]+/, "").trim();
    return rest || null;
  }

  function resolve(currentText, segment, liveSegment) {
    if (!segment || !segment.text) {
      return { text: currentText || "", provisional: Boolean(liveSegment) };
    }

    if (segment.is_final === false) {
      return { text: segment.text, provisional: true };
    }

    if (!liveSegment || !liveSegment.text) {
      return { text: segment.text, provisional: false };
    }

    var finalText = String(segment.text);
    var liveText = String(liveSegment.text);
    var finalNormalized = normalize(finalText);
    var liveNormalized = normalize(liveText);
    var sameTime = Number.isFinite(Number(segment.start_ms))
      && Number.isFinite(Number(liveSegment.start_ms))
      && Math.abs(Number(segment.start_ms) - Number(liveSegment.start_ms)) <= 500;
    var finalIsShorterHypothesis = finalNormalized
      && liveNormalized
      && liveNormalized.indexOf(finalNormalized) === 0
      && finalNormalized.length < liveNormalized.length;
    var olderThanLive = Number.isFinite(Number(segment.end_ms))
      && Number.isFinite(Number(liveSegment.start_ms))
      && Number(segment.end_ms) <= Number(liveSegment.start_ms) + 500;

    // A final caption can be only the completed prefix of the longer live
    // hypothesis. Keep the latter visible (and visibly provisional) instead
    // of making the current-sentence panel jump backwards to that prefix.
    if (finalIsShorterHypothesis || (sameTime && finalText.length < liveText.length)) {
      return { text: liveText, provisional: true };
    }

    // Finals are emitted before the next draft. An older history item should
    // not replace the newer sentence currently being recognized.
    if (olderThanLive) return { text: liveText, provisional: true };

    return { text: finalText, provisional: false };
  }

  return { resolve: resolve, remainder: remainder };
});
