"""Tests for the sliding-window live MLX provider.

The provider exists because the incremental decoder produced word salad on real
accented lecture audio. These tests pin the windowing contract: when it decodes,
what it publishes, and where the timestamps land.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from backend.models import SessionConfig
from backend.providers.base import ProviderError
from backend.providers.mlx_parakeet import MlxModelCache
from backend.providers.mlx_windowed import MlxWindowedParakeetProvider

MODEL_REF = "mlx-community/parakeet-tdt-0.6b-v3"


def make_provider(transcribe, **kwargs):
    kwargs.setdefault("window_seconds", 6.0)
    kwargs.setdefault("hop_seconds", 2.0)
    kwargs.setdefault("head_guard_seconds", 2.0)
    return MlxWindowedParakeetProvider(
        MODEL_REF,
        model_cache=MlxModelCache(loader=lambda **_: object()),
        transcribe=transcribe,
        **kwargs,
    )


def sentence(text, start, end):
    return SimpleNamespace(text=text, start=start, end=end)


def result(*sentences):
    return SimpleNamespace(sentences=list(sentences), text=" ".join(s.text for s in sentences))


def start(provider, language="en"):
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", language, 16000))


def test_provider_decodes_the_first_window_without_waiting_for_a_full_one():
    """The cold start is the first window, not the full window.

    Waiting for a full 18s window meant 18s of silence at the start of every
    lecture. The window ends at the live edge, so early in a session it is simply
    all the audio there is -- the gate was the only thing making it wait.
    """
    calls = []

    def transcribe(window):
        calls.append(window.size)
        return result(sentence("Hello there.", 2.0, 3.0))

    provider = make_provider(transcribe)
    start(provider)

    # window 6s, hop 2s, guard 2s -> the first window is guard + hop = 4s.
    assert provider.first_window_seconds == 4.0

    # 3s is still less than the first window, so nothing can be decoded yet.
    assert provider.push(np.zeros(3 * 16000, dtype=np.float32)) == []
    assert calls == []

    provider.push(np.zeros(1 * 16000, dtype=np.float32))

    assert calls == [4 * 16000]


def test_provider_grows_the_window_to_its_configured_length():
    """The window is capped by the configured length, not fixed at it."""
    calls = []

    def transcribe(window):
        calls.append(window.size)
        return result()

    provider = make_provider(transcribe)
    start(provider)

    # 4s: the window is everything heard so far.
    provider.push(np.zeros(4 * 16000, dtype=np.float32))
    # 6s: still everything heard so far, and now the configured length.
    provider.push(np.zeros(2 * 16000, dtype=np.float32))
    # 8s: the window has reached its length and now slides.
    provider.push(np.zeros(2 * 16000, dtype=np.float32))

    assert calls == [4 * 16000, 6 * 16000, 6 * 16000]


def test_provider_first_window_never_exceeds_the_configured_window():
    provider = make_provider(lambda window: result(), window_seconds=4.0, head_guard_seconds=1.0)

    assert provider.first_window_seconds == 4.0


def test_provider_never_opens_a_window_the_head_guard_would_empty():
    """A first window no longer than the guard would publish nothing at all."""
    provider = make_provider(
        lambda window: result(), window_seconds=30.0, hop_seconds=2.0, head_guard_seconds=4.0
    )

    assert provider.first_window_seconds == 6.0


def test_provider_decodes_once_per_hop_after_the_first_window():
    calls = []

    def transcribe(window):
        calls.append(window.size)
        return result()

    provider = make_provider(transcribe)
    start(provider)

    provider.push(np.zeros(4 * 16000, dtype=np.float32))
    assert len(calls) == 1

    # One second is less than the 2s hop, so the second push is still too early.
    provider.push(np.zeros(1 * 16000, dtype=np.float32))
    assert len(calls) == 1

    provider.push(np.zeros(1 * 16000, dtype=np.float32))
    assert len(calls) == 2


def test_provider_publishes_sentences_past_the_head_guard_with_absolute_times():
    """A window decodes its own start without left context, so that part is held back."""
    def transcribe(window):
        return result(
            sentence("garbled opening", 0.0, 1.5),
            sentence("a real sentence", 2.5, 3.5),
        )

    provider = make_provider(transcribe)
    start(provider)
    segments = provider.push(np.zeros(4 * 16000, dtype=np.float32))

    assert [(item.text, item.is_final) for item in segments] == [("a real sentence", True)]
    assert (segments[0].start_ms, segments[0].end_ms) == (2500, 3500)


def test_provider_offsets_sentences_by_the_window_position():
    """Later windows must report later times, not restart at zero."""
    def transcribe(window):
        return result(sentence("second window", 2.5, 3.5))

    provider = make_provider(transcribe)
    start(provider)

    provider.push(np.zeros(4 * 16000, dtype=np.float32))
    provider.push(np.zeros(2 * 16000, dtype=np.float32))
    segments = provider.push(np.zeros(2 * 16000, dtype=np.float32))

    # The window now ends at 8s and covers 2s..8s, so 2.5s into it is 4.5s.
    assert segments[0].start_ms == 4500
    assert segments[0].end_ms == 5500


def test_provider_flush_publishes_the_tail_without_the_head_guard():
    def transcribe(window):
        return result(sentence("the very last words", 0.0, 1.0))

    provider = make_provider(transcribe)
    start(provider)

    provider.push(np.zeros(4 * 16000, dtype=np.float32))
    # Nothing new since the last decode, so flush has nothing to add.
    assert provider.flush() == []

    provider.push(np.zeros(1 * 16000, dtype=np.float32))
    flushed = provider.flush()

    assert [item.text for item in flushed] == ["the very last words"]


def test_provider_rejects_a_language_parakeet_cannot_transcribe():
    provider = make_provider(lambda window: result())
    with pytest.raises(ProviderError) as error:
        start(provider, language="zh")
    assert error.value.code == "MLX_LANGUAGE_UNSUPPORTED"


def test_provider_releases_the_model_lease_when_closed():
    cache = MlxModelCache(loader=lambda **_: object())
    provider = MlxWindowedParakeetProvider(
        MODEL_REF,
        window_seconds=6.0,
        hop_seconds=2.0,
        model_cache=cache,
        transcribe=lambda window: result(),
    )
    start(provider)

    provider.close()

    # The lease is exclusive: a second acquire only succeeds because close released it.
    cache.acquire(MODEL_REF)
    cache.release(MODEL_REF)


def test_provider_rejects_a_window_shorter_than_the_decoder_can_use():
    with pytest.raises(ValueError):
        MlxWindowedParakeetProvider(MODEL_REF, window_seconds=2.0)


def test_provider_bounds_its_audio_buffer():
    provider = make_provider(lambda window: result())
    start(provider)

    for _ in range(6):
        provider.push(np.zeros(2 * 16000, dtype=np.float32))

    # Only one window is ever re-decoded, so the buffer must not grow with the lecture.
    assert provider._buffer.size <= 6 * 16000
