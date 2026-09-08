import subprocess


def run_node(script):
    return subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_audio_recorder_uses_ten_second_chunks_and_waits_for_writes():
    script = r'''
const chunks = [];
const manifests = [];
const pending = new Map();
const clock = {value: 0, now() { return this.value; }, advance(ms) { this.value += ms; }};

class FakeMediaRecorder {
  constructor(stream, options) { this.stream = stream; this.options = options; this.state = 'inactive'; FakeMediaRecorder.last = this; }
  static isTypeSupported(type) { return type === 'audio/webm;codecs=opus'; }
  start(timeslice) { this.timeslice = timeslice; this.state = 'recording'; }
  emitChunk(text) { this.ondataavailable({data: new Blob([text])}); }
  stop() {
    this.state = 'inactive';
    this.ondataavailable({data: new Blob([])});
    Promise.resolve().then(() => this.onstop());
  }
}
global.MediaRecorder = FakeMediaRecorder;

const { create } = require('./static/audio-recorder.js');
const repository = {
  createRecording: async (options) => { manifests.push({...options, status: 'recording', chunkCount: 0}); return manifests[0]; },
  appendChunk: async (chunk) => {
    chunks.push(chunk);
    if (chunk.sequence === 1) await new Promise((resolve) => pending.set(1, resolve));
  },
  finalizeRecording: async () => ({status: 'ready', chunkCount: chunks.length}),
};

(async () => {
  const recorder = create({stream: {}, sessionId: 's1', repository, now: clock.now.bind(clock)});
  await recorder.start();
  const fake = FakeMediaRecorder.last;
  fake.emitChunk('A');
  clock.advance(10000);
  fake.emitChunk('B');
  const stopPromise = recorder.stop();
  let resolved = false;
  stopPromise.then(() => { resolved = true; });
  await new Promise((resolve) => setImmediate(resolve));
  if (fake.timeslice !== 10000) process.exit(1);
  if (chunks.length !== 2 || chunks[0].sequence !== 0 || chunks[1].sequence !== 1) process.exit(2);
  if (resolved) process.exit(3);
  pending.get(1)();
  const manifest = await stopPromise;
  if (manifest.status !== 'ready' || recorder.getState() !== 'stopped') process.exit(4);
  if (manifests[0].mimeType !== 'audio/webm;codecs=opus') process.exit(5);
})();
'''
    result = run_node(script)
    assert result.returncode == 0, result.stderr or result.stdout
