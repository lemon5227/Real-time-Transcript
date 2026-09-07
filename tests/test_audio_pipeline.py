import base64

import numpy as np
import pytest

from backend.audio_pipeline import AudioWindowBuffer, decode_pcm16_base64


def test_48khz_pcm_is_resampled_to_16khz():
    source = np.zeros(4800, dtype=np.int16).tobytes()
    decoded = decode_pcm16_base64(base64.b64encode(source).decode(), 48000, 20000)
    assert decoded.dtype == np.float32
    assert decoded.shape == (1600,)


def test_invalid_sample_rate_is_rejected():
    with pytest.raises(ValueError, match="sample_rate"):
        decode_pcm16_base64("AA==", 123, 20000)


def test_window_buffer_emits_overlap_windows_and_flushes_tail():
    buffer = AudioWindowBuffer(window_seconds=1.0, overlap_seconds=0.25)
    first = buffer.append(np.zeros(20000, dtype=np.float32), 16000)
    tail = buffer.flush()
    assert first[0].start_ms == 0
    assert first[0].audio.shape[0] == 16000
    assert tail
    assert tail[-1].sequence > first[0].sequence
