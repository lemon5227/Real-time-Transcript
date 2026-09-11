"""Measure how long captions lag behind the audio that produced them.

Push a wav at the live server in realtime and report when the first draft caption
and the first confirmed caption reach the browser.

Usage:
    RTT_AUDIO=speech.wav RTT_LABEL="rc=32" .venv/bin/python tools/measure_caption_latency.py

Use audio of at least 13 seconds containing several sentences. A 5-second clip
never reaches the finalization threshold, so every configuration measures the same
and the comparison is meaningless.

Set no_proxy when a system proxy is running, otherwise the client is routed away
from localhost:
    no_proxy='*' RTT_AUDIO=speech.wav .venv/bin/python tools/measure_caption_latency.py
"""

import base64
import json
import os
import time
import urllib.request
import wave

import numpy as np
import socketio

os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

URL = os.environ.get("RTT_URL", "http://127.0.0.1:5001")
CHUNK_SECONDS = 0.1
TAIL_WAIT = 8.0
AUDIO_PATH = os.environ.get("RTT_AUDIO", "speech.wav")
LABEL = os.environ.get("RTT_LABEL", "run")

start = time.monotonic()
ready = {"done": False}
first_partial = {"t": None}
first_final = {"t": None}
count = {"n": 0}


def now():
    return time.monotonic() - start


sio = socketio.Client()


@sio.on("transcription_ready")
def on_ready(_data):
    ready["done"] = True


@sio.on("transcript_segment")
def on_segment(data):
    count["n"] += 1
    if first_partial["t"] is None:
        first_partial["t"] = now()
    if data.get("is_final") and first_final["t"] is None:
        first_final["t"] = now()


with wave.open(AUDIO_PATH, "rb") as handle:
    rate = handle.getframerate()
    frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")

with urllib.request.urlopen(URL + "/api/capabilities", timeout=10) as response:
    capabilities = json.load(response)
window = capabilities["audio"]["window_seconds"]
streaming_chunk_seconds = capabilities["audio"]["streaming_chunk_seconds"]

sio.connect(URL)
sio.emit(
    "start_transcription",
    {"mode": "local", "model": "parakeet-tdt-0.6b-v3", "language": "en", "sample_rate": 16000},
)

deadline = time.monotonic() + 120
while not ready["done"] and time.monotonic() < deadline:
    time.sleep(0.2)

chunk = int(rate * CHUNK_SECONDS)
scheduled = time.monotonic()
begin = now()
offset_ms = 0
sequence = 1
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
    scheduled += CHUNK_SECONDS
    remaining = scheduled - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)

push_end = now()
time.sleep(TAIL_WAIT)
result = sio.call("stop_transcription", {}, timeout=30)
sio.disconnect()

spoken = len(frames) / float(rate)
print("=== %s | %s ===" % (LABEL, AUDIO_PATH))
print("窗口 window_seconds = %.1f" % window)
print("流式块 streaming_chunk_seconds = %.1f" % streaming_chunk_seconds)
print("  语音时长            : %.2f s" % spoken)
if first_partial["t"] is not None:
    print("  首个字幕延迟        : %.2f s  <- 从开始说话到屏幕上有字" % (first_partial["t"] - begin))
else:
    print("  首个字幕延迟        : 无字幕")
if first_final["t"] is not None:
    print("  首个确认字幕延迟    : %.2f s" % (first_final["t"] - begin))
print("  说话结束后补齐耗时  : %.2f s" % max(0.0, (first_final["t"] or now()) - push_end))
print("  最终段数            : %d / 事件数 %d" % (len(result.get("segments", [])), count["n"]))
