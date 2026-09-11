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

    speech_audio, speech_silent = gate.filter(speech)
    assert np.array_equal(speech_audio, speech)
    assert speech_silent is False

    silent, silent_flag = gate.filter(room_tone)

    assert silent.shape == room_tone.shape
    assert silent.dtype == np.float32
    assert not silent.any()
    assert silent_flag is True
    assert gate.skipped_seconds == pytest.approx(1.0)


def test_disabled_voice_gate_is_a_passthrough():
    gate = VoiceGate(enabled=False)
    room_tone = np.full(8000, 0.0001, dtype=np.float32)

    passed, silent_flag = gate.filter(room_tone)
    assert np.array_equal(passed, room_tone)
    assert silent_flag is False
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


class _RecordingProvider:
    """Stands in for a transcription backend and keeps every window it sees.

    A streaming provider owns a continuous timeline, so it reports offsets that
    keep growing; a windowed provider reports offsets inside one window only.
    """

    name = "recording"
    model = "recording-model"

    def __init__(self, streaming=False):
        self.chunks = []
        self.streaming = streaming
        self.requires_contiguous_audio = streaming
        self._cursor = 0

    def start(self, _config):
        pass

    def push(self, audio):
        self.chunks.append(np.asarray(audio).copy())
        start = self._cursor if self.streaming else 0
        self._cursor += 500
        return [
            TranscriptSegment(
                "vad-%d" % len(self.chunks),
                "caption %d" % len(self.chunks),
                start,
                start + 500,
                True,
            )
        ]

    def flush(self):
        return []

    def close(self):
        pass


def _push_silence_then_speech(manager, sid="sid-1"):
    """One second of near-silence followed by one second of speech.

    The noise floor sits at 8/32768 (about -72 dBFS), below the gate threshold.
    """
    silence = np.zeros(16000, dtype=np.int16)
    silence[:] = 8
    speech = np.zeros(16000, dtype=np.int16)
    speech[:] = 12000
    manager.push_audio(
        sid, base64.b64encode(silence.tobytes()).decode(), 16000, sequence=1, offset_ms=0
    )
    manager.push_audio(
        sid, base64.b64encode(speech.tobytes()).decode(), 16000, sequence=2, offset_ms=1000
    )


def _windowed_config():
    return SessionConfig(
        "local", "fake", "en", 16000, window_seconds=1.0, overlap_seconds=0.5, enable_vad=True
    )


def test_windowed_provider_skips_silent_windows_without_moving_the_timeline():
    """A quiet room must not cost a cloud request or a whisper inference."""
    provider = _RecordingProvider(streaming=False)
    manager = SessionManager(provider_factory=lambda _config: provider)
    manager.start("sid-1", _windowed_config())
    _push_silence_then_speech(manager)
    result = manager.stop("sid-1")

    # With a 1s window and 0.5s overlap the recording holds windows at 0ms,
    # 500ms and 1000ms. Only the first is silent, so two reach the provider.
    assert len(provider.chunks) == 2
    assert np.asarray(provider.chunks[0]).any()
    # Skipping silence must not shift the transcript away from the recording.
    assert [segment["start_ms"] for segment in result["segments"]] == [500, 1000]


def test_streaming_provider_still_receives_silent_windows_as_zeros():
    """A streaming model owns its timeline, so it has to see every window."""
    provider = _RecordingProvider(streaming=True)
    manager = SessionManager(provider_factory=lambda _config: provider)
    manager.start("sid-1", _windowed_config())
    _push_silence_then_speech(manager)
    result = manager.stop("sid-1")

    # A streaming provider takes the recording without overlap, so two seconds
    # of audio are two windows and every one of them has to arrive.
    assert len(provider.chunks) == 2
    # The silent window is handed over as zeros, not as the recorded room tone.
    assert not np.asarray(provider.chunks[0]).any()
    assert np.asarray(provider.chunks[1]).any()
    # Offsets come from the provider's own timeline, which stays continuous.
    assert [segment["start_ms"] for segment in result["segments"]] == [0, 500]


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
