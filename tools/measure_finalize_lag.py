"""Show when the MLX Parakeet stream confirms tokens, and how far behind it runs.

This drives the model directly (no server, no browser) so it isolates the single
biggest contributor to caption lag: the streaming decoder refuses to finalize the
last ``context_size[1]`` encoder frames. One encoder frame is
8 (subsampling) * 160 (hop) / 16000 = 0.08s, so the lag is ``right_context * 0.08``.

Usage:
    .venv/bin/python tools/measure_finalize_lag.py [right_context] [audio.wav]

The audio is looped to at least 20 seconds because a short clip never reaches the
finalization threshold, which makes the model look like it produces nothing.
"""

import sys
import wave

import mlx.core as mx
import numpy as np
from parakeet_mlx import from_pretrained

SAMPLE_RATE = 16000
MODEL_REF = "mlx-community/parakeet-tdt-0.6b-v3"
MIN_SECONDS = 20.0
DEFAULT_AUDIO = "speech.wav"


def load_audio(path: str) -> np.ndarray:
    with wave.open(path, "rb") as handle:
        if handle.getframerate() != SAMPLE_RATE:
            raise SystemExit(
                "expected %d Hz, got %d Hz -- convert with `afconvert -f WAVE "
                "-d LEI16@16000 -c 1 in.aiff out.wav`" % (SAMPLE_RATE, handle.getframerate())
            )
        pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    repeats = max(1, int(np.ceil(MIN_SECONDS * SAMPLE_RATE / max(1, audio.size))))
    return np.tile(audio, repeats)


def main() -> None:
    right_context = int(sys.argv[1]) if len(sys.argv) > 1 else 32
    path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_AUDIO
    audio = load_audio(path)
    print(
        "audio: %.2fs  right_context=%d  expected_lag=%.2fs"
        % (audio.size / SAMPLE_RATE, right_context, right_context * 0.08)
    )

    model = from_pretrained(MODEL_REF)
    stream = model.transcribe_stream(
        context_size=(256, right_context), keep_original_attention=False
    )
    stream.__enter__()
    print("drop_size=%d frames" % stream.drop_size)
    print("-" * 78)

    first_final_at = None
    for start in range(0, audio.size, SAMPLE_RATE):
        block = audio[start : start + SAMPLE_RATE]
        stream.add_audio(mx.array(block))
        now = (start + block.size) / SAMPLE_RATE
        finalized = list(stream.finalized_tokens)
        draft = list(stream.draft_tokens)
        if finalized and first_final_at is None:
            first_final_at = now
        print(
            "t=%5.1fs | finalized=%3d draft=%3d | FINAL: %r"
            % (now, len(finalized), len(draft), "".join(t.text for t in finalized)[-60:])
        )
        print("          | DRAFT: %r" % "".join(t.text for t in draft)[-60:])

    print("-" * 78)
    print("first finalized token at t=%ss" % first_final_at)


if __name__ == "__main__":
    main()
