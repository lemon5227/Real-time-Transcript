(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.EchoAudioBuffer = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function PcmChunkBuffer(targetSeconds) {
    this.targetSeconds = Number(targetSeconds) > 0 ? Number(targetSeconds) : 0.5;
    this._chunks = [];
    this._samples = 0;
    this._sampleRate = 16000;
  }

  PcmChunkBuffer.prototype.push = function (buffer, sampleRate) {
    if (!buffer || !buffer.byteLength) return [];
    var usableBytes = buffer.byteLength - (buffer.byteLength % 2);
    if (!usableBytes) return [];
    var samples = new Int16Array(buffer, 0, usableBytes / 2);
    this._sampleRate = Number(sampleRate) > 0 ? Number(sampleRate) : this._sampleRate;
    this._chunks.push(new Int16Array(samples));
    this._samples += samples.length;
    if (this._samples < Math.max(1, Math.round(this._sampleRate * this.targetSeconds))) return [];
    return [this.flush()];
  };

  PcmChunkBuffer.prototype.flush = function () {
    if (!this._samples) return null;
    var output = new Int16Array(this._samples);
    var offset = 0;
    this._chunks.forEach(function (chunk) {
      output.set(chunk, offset);
      offset += chunk.length;
    });
    this._chunks = [];
    this._samples = 0;
    return output.buffer;
  };

  PcmChunkBuffer.prototype.reset = function () {
    this._chunks = [];
    this._samples = 0;
  };

  return { PcmChunkBuffer: PcmChunkBuffer };
});
