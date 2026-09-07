import base64
import binascii
from dataclasses import dataclass
from typing import List

import numpy as np

from .models import SUPPORTED_SAMPLE_RATES

TARGET_SAMPLE_RATE = 16000


def resample_audio(
    audio_array: np.ndarray, src_rate: int, target_rate: int = TARGET_SAMPLE_RATE
) -> np.ndarray:
    if src_rate not in SUPPORTED_SAMPLE_RATES:
        raise ValueError("sample_rate is not supported")
    if target_rate <= 0:
        raise ValueError("target_rate must be positive")

    audio = np.asarray(audio_array, dtype=np.float32)
    if audio.ndim == 0:
        audio = audio.reshape(0)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1, dtype=np.float32)
    audio = audio.reshape(-1)
    if audio.size == 0 or src_rate == target_rate:
        return audio.astype(np.float32, copy=False)

    target_length = max(1, int(round(audio.shape[0] * target_rate / src_rate)))
    x_old = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32, copy=False)


def decode_pcm16_base64(encoded: str, sample_rate: int, max_bytes: int) -> np.ndarray:
    if sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise ValueError("sample_rate is not supported")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not encoded:
        return np.empty(0, dtype=np.float32)

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("audio is not valid base64") from exc
    if len(raw) > max_bytes:
        raise ValueError("audio payload is too large")
    if len(raw) % 2:
        raise ValueError("PCM16 payload must contain an even number of bytes")

    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    pcm /= 32768.0
    return resample_audio(pcm, sample_rate)


@dataclass(frozen=True)
class AudioWindow:
    start_ms: int
    audio: np.ndarray
    sequence: int


class AudioWindowBuffer:
    def __init__(self, window_seconds: float = 3.0, overlap_seconds: float = 0.5):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if overlap_seconds < 0 or overlap_seconds >= window_seconds:
            raise ValueError("overlap_seconds must be less than window_seconds")
        self.window_samples = max(1, round(window_seconds * TARGET_SAMPLE_RATE))
        self.overlap_samples = max(0, round(overlap_seconds * TARGET_SAMPLE_RATE))
        self.hop_samples = self.window_samples - self.overlap_samples
        self._buffer = np.empty(0, dtype=np.float32)
        self._buffer_start_ms = 0
        self._sequence = 0
        self._emitted_window = False

    def append(self, audio: np.ndarray, sample_rate: int) -> List[AudioWindow]:
        normalized = resample_audio(audio, sample_rate)
        if normalized.size:
            self._buffer = np.concatenate((self._buffer, normalized))

        windows: List[AudioWindow] = []
        while self._buffer.size >= self.window_samples:
            windows.append(
                AudioWindow(
                    start_ms=self._buffer_start_ms,
                    audio=self._buffer[: self.window_samples].copy(),
                    sequence=self._sequence,
                )
            )
            self._sequence += 1
            self._buffer = self._buffer[self.hop_samples :]
            self._buffer_start_ms += round(self.hop_samples * 1000 / TARGET_SAMPLE_RATE)
            self._emitted_window = True
        return windows

    def flush(self) -> List[AudioWindow]:
        if not self._buffer.size:
            return []

        if self._emitted_window:
            tail = self._buffer[self.overlap_samples :]
            start_ms = self._buffer_start_ms + round(
                self.overlap_samples * 1000 / TARGET_SAMPLE_RATE
            )
        else:
            tail = self._buffer
            start_ms = self._buffer_start_ms

        self._buffer = np.empty(0, dtype=np.float32)
        if tail.size < round(0.2 * TARGET_SAMPLE_RATE):
            return []

        window = AudioWindow(start_ms=start_ms, audio=tail.copy(), sequence=self._sequence)
        self._sequence += 1
        return [window]
