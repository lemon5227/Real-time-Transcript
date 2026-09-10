import base64

import numpy as np
import pytest

from backend.models import SessionConfig, TranscriptSegment
from backend.session_manager import SessionManager
from backend.voice_gate import DEFAULT_SILENCE_RMS, VoiceGate, normalize_threshold, rms


def test_voice_gate_keeps_speech_and_zeroes_the_noise_floor():
    gate = VoiceGate(enabled=True, threshold=0.01)
    speech = np.full(16000, 0.2, dtype=np.float32)
    room_tone = np.full(16000, 0.001, dtype=np.float32)

    assert np.array_equal(gate.filter(speech), speech)
    silent = gate.filter(room_tone)

    assert silent.shape == room_tone.shape
    assert silent.dtype == np.float32
    assert not silent.any()
    assert gate.skipped_seconds == pytest.approx(1.0)


def test_disabled_voice_gate_is_a_passthrough():
    gate = VoiceGate(enabled=False)
    room_tone = np.full(8000, 0.0001, dtype=np.float32)

    assert np.array_equal(gate.filter(room_tone), room_tone)
    assert gate.skipped_seconds == 0.0


def test_voice_gate_threshold_is_bounded_and_tolerant():
    assert normalize_threshold(None) == DEFAULT_SILENCE_RMS
    assert normalize_threshold("not-a-number") == DEFAULT_SILENCE_RMS
    assert normalize_threshold(float("nan")) == DEFAULT_SILENCE_RMS
    assert normalize_threshold(-1) == 0.0
    assert normalize_threshold(9) == 0.05
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0
    # A full-scale square wave has an RMS of 1.0.
    assert rms(np.ones(1000, dtype=np.float32)) == pytest.approx(1.0)


def test_silent_windows_reach_the_provider_as_silence_without_moving_the_timeline():
    class RecordingProvider:
        name = "recording"
        model = "recording-model"

        def __init__(self):
            self.chunks = []

        def start(self, _config):
            pass

        def push(self, audio):
            self.chunks.append(np.asarray(audio).copy())
            return [TranscriptSegment("vad-%d" % len(self.chunks), "x", 0, 500, True)]

        def flush(self):
            return []

        def close(self):
            pass

    provider = RecordingProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    config = SessionConfig(
        "local",
        "fake",
        "en",
        16000,
        window_seconds=1.0,
        overlap_seconds=0.5,
        enable_vad=True,
    )
    manager.start("sid-1", config)

    # One second of near-silence followed by one second of speech. The noise
    # floor sits at 8/32768 (about -72 dBFS), below the gate threshold.
    silence = np.zeros(16000, dtype=np.int16)
    silence[:] = 8
    speech = np.zeros(16000, dtype=np.int16)
    speech[:] = 12000
    manager.push_audio(
        "sid-1", base64.b64encode(silence.tobytes()).decode(), 16000, sequence=1, offset_ms=0
    )
    manager.push_audio(
        "sid-1", base64.b64encode(speech.tobytes()).decode(), 16000, sequence=2, offset_ms=1000
    )
    result = manager.stop("sid-1")

    assert provider.chunks, "the provider must still receive every window"
    # The silent window is handed over as zeros, not as the recorded room tone.
    assert not np.asarray(provider.chunks[0]).any()
    assert np.asarray(provider.chunks[1]).any()
    # Skipping silence must not shift the transcript away from the recording.
    # With a 1s window and 0.5s overlap the provider still sees windows at
    # 0ms, 500ms and 1000ms of the recording timeline.
    assert [segment["start_ms"] for segment in result["segments"]] == [0, 500, 1000]


def test_disabled_vad_delivers_the_original_audio():
    class RecordingProvider:
        name = "recording"
        model = "recording-model"

        def __init__(self):
            self.chunks = []

        def start(self, _config):
            pass

        def push(self, audio):
            self.chunks.append(np.asarray(audio).copy())
            return []

        def flush(self):
            return []

        def close(self):
            pass

    provider = RecordingProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    config = SessionConfig(
        "local",
        "fake",
        "en",
        16000,
        window_seconds=1.0,
        overlap_seconds=0.0,
        enable_vad=False,
    )
    manager.start("sid-1", config)
    silence = np.zeros(16000, dtype=np.int16)
    silence[:] = 8
    manager.push_audio(
        "sid-1", base64.b64encode(silence.tobytes()).decode(), 16000, sequence=1, offset_ms=0
    )
    manager.stop("sid-1")

    assert np.asarray(provider.chunks[0]).any()
