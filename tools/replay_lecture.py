"""Replay a lecture recording through the server at realtime pace.

This is the measurement that matters for live captions: a real recording, pushed
at the speed it was spoken, through the same socket API the browser uses. It
reports what each caption said, how far behind the audio it ran, and -- because
the merger reuses a segment id when it replaces an earlier decode -- how many
captions the reader actually ends up with.

Two numbers here are not the same thing and are reported separately:

* the **wait before a caption appears**, measured against the audio that caption
  covers, which is what the architecture controls; and
* the **cold start**, the wall-clock time until the first caption of all. This is
  the silence at the start of a session, and it is the only cost of a long
  window -- a window that ends at the live edge costs nothing per caption.

It also counts captions that still overlap each other, which needs no reference
transcript and is the closest thing to "is this readable" that can be measured.

Prepare the audio as 16 kHz mono, which is what the capture path sends:

    ffmpeg -i lecture.webm -ac 1 -ar 16000 -vn /tmp/lecture.wav

Usage:
    no_proxy='*' .venv/bin/python tools/replay_lecture.py /tmp/lecture.wav

Start the server first. Latency is measured against the wall clock, so a busy
machine shows up in the numbers -- that is the point.
"""

import argparse
import base64
import os
import re
import sys
import threading
import time
import wave

import numpy as np
import socketio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models import TranscriptSegment  # noqa: E402
from backend.segment_merger import covers_the_same_speech  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:5001"
CHUNK_SECONDS = 1.0
SAMPLE_RATE = 16000
# How long to keep draining after the last chunk, so the final window decodes
# and the tail of the lecture is not silently dropped from the measurement.
DRAIN_SECONDS = 6.0


def overlapping_captions(payloads: list) -> list:
    """Pairs of captions that still describe the same speech."""
    segments = [
        TranscriptSegment(
            id=payload.get("id") or "?",
            text=payload.get("text") or "",
            start_ms=payload.get("start_ms") or 0,
            end_ms=payload.get("end_ms") or 0,
            is_final=True,
        )
        for payload in payloads
    ]
    pairs = []
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            if covers_the_same_speech(segments[i], segments[j]):
                pairs.append((segments[i], segments[j]))
    return pairs


def repeated_phrases(text: str, length: int = 5) -> int:
    """How many distinct phrases of ``length`` words the transcript says twice.

    Overlap in *time* is not the only way a duplicate reaches the reader: the
    same sentence can also come back at a different timestamp, which the
    coverage rule cannot see because the two spans do not overlap. A phrase the
    lecturer said once appearing twice is unreadable regardless of its
    timestamps, so it is counted separately.
    """
    tokens = [token for token in re.split(r"[^a-z0-9]+", text.casefold()) if token]
    counts = {}
    for index in range(len(tokens) - length + 1):
        gram = tuple(tokens[index : index + length])
        counts[gram] = counts.get(gram, 0) + 1
    return sum(1 for count in counts.values() if count > 1)


def load_audio(path: str) -> tuple:
    with wave.open(path, "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    if channels != 1:
        raise SystemExit("%s has %d channels; convert to mono first" % (path, channels))
    if rate != SAMPLE_RATE:
        raise SystemExit(
            "%s is %d Hz; convert to %d Hz first (ffmpeg -ar %d)"
            % (path, rate, SAMPLE_RATE, SAMPLE_RATE)
        )
    return np.frombuffer(raw, dtype="<i2").reshape(-1), rate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", help="16 kHz mono wav to replay")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--mode", default="local", choices=["local", "auto", "cloud"])
    parser.add_argument("--model", default="parakeet-tdt-0.6b-v3")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--show-every-event",
        action="store_true",
        help="print each revision as it arrives instead of only the final caption",
    )
    args = parser.parse_args()

    samples, rate = load_audio(args.wav)
    total_seconds = samples.size / rate

    received = []
    errors = []
    ready = threading.Event()
    finished = threading.Event()
    stop_result = {}

    sio = socketio.Client(reconnection=False)

    @sio.on("transcription_ready")
    def _on_ready(_payload):
        ready.set()

    @sio.on("transcript_segment")
    def _on_segment(payload):
        received.append((time.monotonic(), "transcript_segment", payload))

    @sio.on("transcript_segment_removed")
    def _on_segment_removed(payload):
        # The server retracted a caption the merger folded into a neighbour.
        # The reader's transcript loses it, so this measurement has to as well.
        received.append((time.monotonic(), "transcript_segment_removed", payload))

    @sio.on("transcription_error")
    def _on_error(payload):
        errors.append(payload)

    @sio.on("transcription_stopped")
    def _on_stopped(payload):
        stop_result.update(payload or {})
        finished.set()

    sio.connect(args.url, transports=["polling"])
    sio.emit(
        "start_transcription",
        {
            "mode": args.mode,
            "model": args.model,
            "language": args.language,
            "sample_rate": rate,
            "enable_vad": True,
        },
    )
    if not ready.wait(timeout=180):
        print("model never became ready; errors=%s" % errors, file=sys.stderr)
        return 1
    print("ready; replaying %.1fs of audio" % total_seconds, flush=True)

    started = time.monotonic()
    chunk = int(CHUNK_SECONDS * rate)
    sequence = 0
    for offset in range(0, samples.size, chunk):
        piece = samples[offset : offset + chunk]
        if piece.size == 0:
            continue
        drift = offset / rate - (time.monotonic() - started)
        if drift > 0:
            time.sleep(drift)
        sio.emit(
            "audio_chunk",
            {
                "audio": base64.b64encode(piece.tobytes()).decode("ascii"),
                "sample_rate": rate,
                "sequence": sequence,
                "offset_ms": int(offset / rate * 1000),
            },
        )
        sequence += 1

    time.sleep(DRAIN_SECONDS)
    sio.emit("stop_transcription", {})
    finished.wait(timeout=120)
    time.sleep(1)
    sio.disconnect()

    finals = []
    by_id = {}
    last_wall = {}
    revisions = 0
    retracted = 0
    first_lags = []
    final_lags = []
    cold_start = None
    cold_start_covers_ms = 0
    for wall, event, payload in received:
        if event == "transcript_segment_removed":
            removed_id = str(payload.get("id") or payload.get("segment_id") or "?")
            if by_id.pop(removed_id, None) is not None:
                retracted += 1
            last_wall.pop(removed_id, None)
            if args.show_every_event:
                print("[retracted            %s] %s" % (removed_id, "caption withdrawn"), flush=True)
            continue
        text = (payload.get("text") or "").strip()
        if not text:
            continue
        end_ms = payload.get("end_ms") or 0
        lag = wall - started - end_ms / 1000
        segment_id = payload.get("id") or "?"
        if payload.get("is_final"):
            if segment_id in by_id:
                revisions += 1
            else:
                # When the reader first sees this caption. This is the latency
                # that matters: the text is on screen from here on, and only its
                # wording improves afterwards.
                if lag > -5:
                    first_lags.append(lag)
                if cold_start is None:
                    # Silence at the start of the session. Measured against the
                    # wall clock, not against the audio, because there is no
                    # audio on screen yet -- that is the whole point.
                    cold_start = wall - started
                    cold_start_covers_ms = end_ms
            by_id[segment_id] = payload
            last_wall[segment_id] = wall
            finals.append(payload)
        if args.show_every_event:
            print(
                "[%s %6d-%6dms lag %5.1fs %s] %s"
                % (
                    "final" if payload.get("is_final") else "draft",
                    payload.get("start_ms") or 0,
                    end_ms,
                    lag,
                    segment_id,
                    text,
                ),
                flush=True,
            )

    # The lag of a caption's last revision is not a delay the reader feels: a
    # sentence that ended early in the window is republished, with better
    # wording, when the window containing it is decoded. Report it, but do not
    # confuse it with the wait before anything appeared.
    for segment_id, payload in by_id.items():
        lag = last_wall[segment_id] - started - (payload.get("end_ms") or 0) / 1000
        if lag > -5:
            final_lags.append(lag)

    print(
        "\n=== captions as the reader ends up seeing them "
        "(%d events, %d captions, %d in-place revisions, %d retracted) ==="
        % (len(finals), len(by_id), revisions, retracted),
        flush=True,
    )
    for payload in sorted(by_id.values(), key=lambda item: item.get("start_ms") or 0):
        print(
            "[%6d-%6dms] %s"
            % (payload.get("start_ms") or 0, payload.get("end_ms") or 0, payload.get("text")),
            flush=True,
        )

    print("\n=== readability ===", flush=True)
    final_text = " ".join((payload.get("text") or "") for payload in by_id.values())
    pairs = overlapping_captions(list(by_id.values()))
    print("captions that still overlap another: %d" % len(pairs), flush=True)
    for left, right in pairs:
        print(
            "  [%6d-%6dms] %s\n  [%6d-%6dms] %s"
            % (left.start_ms, left.end_ms, left.text, right.start_ms, right.end_ms, right.text),
            flush=True,
        )
    print("phrases said twice:                  %d" % repeated_phrases(final_text), flush=True)

    print("\n=== latency ===", flush=True)
    if cold_start is not None:
        print(
            "cold start:                    first caption after %.1fs "
            "(covering audio up to %.1fs)" % (cold_start, cold_start_covers_ms / 1000),
            flush=True,
        )
    if first_lags:
        print(
            "wait before a caption appears: median %.1fs, max %.1fs"
            % (float(np.median(first_lags)), max(first_lags)),
            flush=True,
        )
    if final_lags:
        print(
            "lag of the final wording:      median %.1fs, max %.1fs"
            % (float(np.median(final_lags)), max(final_lags)),
            flush=True,
        )
    print("errors: %s" % errors, flush=True)
    print("segments kept by the session: %s" % len(stop_result.get("segments") or []), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
