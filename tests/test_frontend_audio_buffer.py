import subprocess


def test_frontend_audio_buffer_batches_pcm_and_flushes_tail():
    script = r'''
const { PcmChunkBuffer } = require('./static/audio-buffer.js');
const buffer = new PcmChunkBuffer(0.5);
const half = new Int16Array(4000).buffer;
const tail = new Int16Array(1000).buffer;

if (buffer.push(half, 16000).length !== 0) process.exit(1);
const batched = buffer.push(half, 16000);
if (batched.length !== 1 || batched[0].byteLength !== 16000) process.exit(2);
buffer.push(tail, 16000);
const flushed = buffer.flush();
if (!flushed || flushed.byteLength !== 2000) process.exit(3);
'''
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
