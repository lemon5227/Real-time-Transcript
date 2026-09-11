"""Measure caption-to-Chinese latency through the real translation path.

Reimplements the browser queue's batching in the client so the batch size and
debounce can be varied without touching the UI.

Usage:
    RTT_BATCH=2 RTT_DEBOUNCE=150 .venv/bin/python tools/measure_translation_latency.py

Set no_proxy when a system proxy is running.
"""

import base64
import os
import time
import wave

import numpy as np
import socketio

os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

URL = os.environ.get("RTT_URL", "http://127.0.0.1:5001")
AUDIO_PATH = os.environ.get("RTT_AUDIO", "speech.wav")
CHUNK_SECONDS = 0.1
TAIL_WAIT = 8.0
BATCH_SIZE = int(os.environ.get("RTT_BATCH", "2"))
DEBOUNCE_MS = int(os.environ.get("RTT_DEBOUNCE", "150"))

start = time.monotonic()
ready = {"done": False}
pending = []
scheduled = {"at": None}
stats = {"final_events": [], "translation_events": [], "translation_errors": []}


def now():
    return time.monotonic() - start


sio = socketio.Client()


@sio.on("transcription_ready")
def on_ready(_data):
    ready["done"] = True


@sio.on("transcript_segment")
def on_segment(data):
    if not data.get("is_final"):
        return
    stats["final_events"].append((now(), data.get("id"), data.get("text")))
    pending.append((data.get("id"), data.get("text")))
    if scheduled["at"] is None:
        scheduled["at"] = time.monotonic() + DEBOUNCE_MS / 1000.0


@sio.on("translation_result")
def on_translation(data):
    stats["translation_events"].append((now(), data))


@sio.on("translation_error")
def on_translation_error(data):
    stats.setdefault("translation_errors", []).append((now(), data))


with wave.open(AUDIO_PATH, "rb") as handle:
    rate = handle.getframerate()
    frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")

sio.connect(URL)
sio.emit(
    "start_transcription",
    {"mode": "local", "model": "parakeet-tdt-0.6b-v3", "language": "en", "sample_rate": 16000},
)

deadline = time.monotonic() + 120
while not ready["done"] and time.monotonic() < deadline:
    time.sleep(0.2)

def maybe_flush():
    """Mimic the browser queue: small batch, short debounce, finals only."""
    global pending, sent
    if not pending or not scheduled["at"]:
        return
    if time.monotonic() < scheduled["at"]:
        return
    batch = pending[:BATCH_SIZE]
    pending = pending[BATCH_SIZE:]
    scheduled["at"] = time.monotonic() + DEBOUNCE_MS / 1000.0 if pending else None
    sent += 1
    sio.emit(
        "translate_segments",
        {
            "segment_ids": [item[0] for item in batch],
            "source_language": "en",
            "target_language": "zh",
            "mode": "fast",
            "provider": "auto",
        },
    )


chunk = int(rate * CHUNK_SECONDS)
sched = time.monotonic()
begin = now()
offset_ms = 0
sequence = 1
sent = 0
for index in range(0, len(frames), chunk):
    block = frames[index : index + chunk]
    sio.emit(
        "audio_chunk",
        {
            "audio": base64.b64encode(block.tobytes()).decode(),
            "sample_rate": rate,
            "sequence": sequence,
            "offset_ms": offset_ms,
        },
    )
    sequence += 1
    offset_ms += int(len(block) * 1000 / rate)
    sched += CHUNK_SECONDS
    remaining = sched - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)

    maybe_flush()

push_end = now()
# Keep servicing the queue while the model finishes the tail.
tail_deadline = time.monotonic() + TAIL_WAIT
while time.monotonic() < tail_deadline:
    maybe_flush()
    time.sleep(0.02)
sio.call("stop_transcription", {}, timeout=30)
sio.disconnect()

print(f"=== batchSize={BATCH_SIZE} debounce={DEBOUNCE_MS}ms ===")
print(f"  语音时长           : {len(frames) / rate:.2f} s")
print(f"  确认字幕条数       : {len(stats['final_events'])}")
for at, seg_id, text in stats["final_events"]:
    print(f"    {at:6.2f}s  {seg_id}  {text[:58]!r}")
print(f"  发出翻译请求       : {sent} 次  (旧配置同口径需要 {-(-len(stats['final_events']) // 5)} 次)")
print(f"  收到翻译结果       : {len(stats['translation_events'])} 次")
first_translation = stats["translation_events"][0][0] if stats["translation_events"] else None
if stats["final_events"] and first_translation is not None:
    first_final_at = stats["final_events"][0][0]
    print(f"  首个确认字幕       : {first_final_at - begin:6.2f} s")
    print(f"  首个译文           : {first_translation - begin:6.2f} s")
    print(f"  字幕→译文 追加耗时  : {first_translation - first_final_at:6.2f} s")
else:
    print("  首个译文           : 无")
for at, data in stats["translation_events"][:3]:
    items = data.get("translations") or []
    texts = " | ".join(item.get("text", "")[:30] for item in items)
    print(f"    {at:6.2f}s  [{data.get('provider')}] {texts}")
for at, data in stats["translation_errors"]:
    print(f"    {at:6.2f}s  翻译失败: {data}")
print(f"  实际传输           : {sio.transport()}")
