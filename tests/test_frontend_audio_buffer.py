import subprocess


def test_frontend_audio_buffer_batches_pcm_and_flushes_tail():
    script = r'''
const { PcmChunkBuffer } = require('./static/audio-buffer.js');
const buffer = new PcmChunkBuffer(0.5);
const half = new Int16Array(4000).buffer;
const tail = new Int16Array(1000).buffer;

if (buffer.push(half, 16000).length !== 0) process.exit(1);
const batched = buffer.push(half, 16000);
if (batched.length !== 1 || batched[0].buffer.byteLength !== 16000) process.exit(2);
buffer.push(tail, 16000);
const flushed = buffer.flush();
if (!flushed || flushed.buffer.byteLength !== 2000) process.exit(3);
'''
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_frontend_audio_buffer_reports_capture_offset_of_each_flush():
    script = r'''
const { PcmChunkBuffer } = require('./static/audio-buffer.js');
const buffer = new PcmChunkBuffer(0.5);
const block = new Int16Array(4000).buffer;

// The first block arrives 8s into the recording and is flushed together with the
// second, so the emitted chunk must keep the earlier capture position.
buffer.push(block, 16000, 8000);
const batched = buffer.push(block, 16000, 8500);
if (batched.length !== 1) process.exit(1);
if (batched[0].offsetMs !== 8000) process.exit(2);

// A later flush moves on to its own capture position.
buffer.push(block, 16000, 12400);
const later = buffer.push(block, 16000, 12900);
if (later.length !== 1 || later[0].offsetMs !== 12400) process.exit(3);

// Reset clears the pending capture position instead of leaking it forward.
buffer.reset();
if (buffer.flush() !== null) process.exit(4);
buffer.push(block, 16000, 600);
const afterReset = buffer.push(block, 16000, 1100);
if (afterReset.length !== 1 || afterReset[0].offsetMs !== 600) process.exit(5);

// Callers without capture offsets keep working.
const plain = new PcmChunkBuffer(0.5);
plain.push(block, 16000);
const plainBatch = plain.push(block, 16000);
if (plainBatch.length !== 1 || plainBatch[0].offsetMs !== null) process.exit(6);
'''
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
