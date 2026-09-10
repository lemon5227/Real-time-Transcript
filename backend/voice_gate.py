"""Cheap energy gate for microphone silence.

Classroom audio contains long stretches with no speech: page turns, writing on a
whiteboard, a break in the lecture. Running those through the model costs battery
and latency without adding a single word. The gate reports such windows so the
session can hand the provider digital silence instead: the audio the model sees
keeps its length, so the transcript timeline stays truthful.
"""

from __future__ import annotations

import numpy as np

# Roughly -66 dBFS. Deliberately conservative: it must only catch the noise floor
# of a quiet room, never a softly spoken sentence.
DEFAULT_SILENCE_RMS = 0.0005

SILENCE_RMS_MIN = 0.0
SILENCE_RMS_MAX = 0.05


def normalize_threshold(value: object, default: float = DEFAULT_SILENCE_RMS) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed == float("inf") or parsed == float("-inf"):
        return default
    return min(SILENCE_RMS_MAX, max(SILENCE_RMS_MIN, parsed))


def rms(audio: np.ndarray) -> float:
    array = np.asarray(audio, dtype=np.float32).reshape(-1)
    if array.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))


class VoiceGate:
    """Replaces near-silent audio with zeros while keeping its duration."""

    def __init__(self, enabled: bool = True, threshold: float = DEFAULT_SILENCE_RMS):
        self.enabled = bool(enabled)
        self.threshold = normalize_threshold(threshold)
        self.skipped_seconds = 0.0

    def filter(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        array = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not self.enabled or array.size == 0:
            return array
        if rms(array) > self.threshold:
            return array
        self.skipped_seconds += array.size / float(sample_rate or 16000)
        # Same shape and dtype, so the provider advances its timeline as if it
        # had received the real silence.
        return np.zeros_like(array)
