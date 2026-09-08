import subprocess


def run_node(script):
    return subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_audio_repository_orders_and_deduplicates_chunks():
    script = r'''
const { createAudioRepository } = require('./static/audio-storage.js');

const manifests = new Map();
const chunks = new Map();
const memoryBackend = {
  createManifest: async (manifest) => {
    manifests.set(manifest.sessionId, {...manifest});
    return manifests.get(manifest.sessionId);
  },
  getManifest: async (sessionId) => manifests.get(sessionId) || null,
  putChunk: async (chunk) => chunks.set(chunk.sessionId + ':' + chunk.sequence, {...chunk}),
  listChunks: async (sessionId) => Array.from(chunks.values()).filter((chunk) => chunk.sessionId === sessionId),
  delete: async (sessionId) => {
    manifests.delete(sessionId);
    for (const key of Array.from(chunks.keys())) if (key.startsWith(sessionId + ':')) chunks.delete(key);
  }
};

(async () => {
  const repository = createAudioRepository({backend: memoryBackend});
  await repository.createRecording({
    sessionId: 'session-1',
    mimeType: 'audio/webm;codecs=opus',
    extension: 'webm',
    startedAt: '2026-09-08T10:00:00.000Z'
  });
  await repository.appendChunk({sessionId: 'session-1', sequence: 1, blob: new Blob(['B']), startMs: 10000, endMs: 20000});
  await repository.appendChunk({sessionId: 'session-1', sequence: 0, blob: new Blob(['A']), startMs: 0, endMs: 10000});
  await repository.appendChunk({sessionId: 'session-1', sequence: 0, blob: new Blob(['A-duplicate']), startMs: 0, endMs: 10000});
const manifest = await repository.finalizeRecording({sessionId: 'session-1', durationMs: 20000});
  const playable = await repository.getPlayableBlob('session-1');
  if (manifest.status !== 'ready' || manifest.chunkCount !== 2 || manifest.bytes !== 2) process.exit(1);
  if (await playable.text() !== 'AB') process.exit(2);
})();
'''
    result = run_node(script)
    assert result.returncode == 0, result.stderr or result.stdout


def test_audio_repository_preserves_partial_failure_status():
    script = r'''
const { createAudioRepository } = require('./static/audio-storage.js');
const manifests = new Map();
const memoryBackend = {
  createManifest: async manifest => { manifests.set(manifest.sessionId, {...manifest}); return manifests.get(manifest.sessionId); },
  getManifest: async sessionId => manifests.get(sessionId) || null,
  putChunk: async () => {},
  listChunks: async () => [],
  delete: async () => {}
};
(async () => {
  const repository = createAudioRepository({backend: memoryBackend});
  await repository.createRecording({sessionId: 'partial', mimeType: 'audio/webm', extension: 'webm'});
  const manifest = await repository.finalizeRecording({sessionId: 'partial', durationMs: 5000, status: 'partial', error: 'disk full'});
  if (manifest.status !== 'partial' || manifest.error !== 'disk full' || manifest.durationMs !== 5000) process.exit(1);
})();
'''
    result = run_node(script)
    assert result.returncode == 0, result.stderr or result.stdout


def test_session_normalization_preserves_audio_manifest_and_legacy_records():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('./static/storage.js', 'utf8'), context);
const oldRecord = context.window.EchoStore.normalizeSession({id: 'old', segments: []});
const newRecord = context.window.EchoStore.normalizeSession({
  id: 'new',
  segments: [{id: 'segment-1', text: 'hello', translations: {zh: {text: '你好', status: 'ready', provider: 'google'}}}],
  audio: {enabled: true, status: 'ready', storage: 'opfs', mimeType: 'audio/webm', chunkCount: 2, bytes: 42, durationMs: 20000}
});
if (oldRecord.audio !== null) process.exit(1);
if (!newRecord.audio || newRecord.audio.status !== 'ready' || newRecord.audio.chunkCount !== 2 || newRecord.audio.bytes !== 42) process.exit(2);
if (!newRecord.segments[0].translations.zh || newRecord.segments[0].translations.zh.text !== '你好') process.exit(3);
'''
    result = run_node(script)
    assert result.returncode == 0, result.stderr or result.stdout
