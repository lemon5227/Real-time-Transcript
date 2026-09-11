import base64
import queue
import threading
import time

import pytest

from backend.models import SessionConfig, TranscriptSegment
from backend.providers.base import ProviderError
from backend.session_manager import AdaptiveStreamingChunkPolicy, SessionManager


def _speech(frames):
    """Base64 PCM16 of audible speech at about -12 dBFS.

    The voice gate now drops near-silent windows before the provider sees them,
    so tests that exercise transcription have to push audio with real level
    instead of the digital silence these used to send.
    """
    return base64.b64encode(b"\x40\x1f" * frames).decode()  # 8000 as int16 LE


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def start(self, config):
        self.started = True

    def push(self, audio):
        return []

    def flush(self):
        return []

    def close(self):
        self.closed = True


def test_start_returns_before_provider_is_ready_and_replays_queued_audio():
    started = threading.Event()
    release = threading.Event()
    received = []
    result_holder = []

    class DelayedProvider(FakeProvider):
        def start(self, _config):
            started.set()
            assert release.wait(timeout=1)

        def push(self, audio):
            received.append(audio.copy())
            return []

    events = []
    manager = SessionManager(
        provider_factory=lambda _config: DelayedProvider(),
        emit=lambda _sid, event, payload: events.append((event, payload)),
    )
    config = SessionConfig(
        "local", "fake", "en", 16000, window_seconds=0.2, overlap_seconds=0.0
    )

    start_thread = threading.Thread(
        target=lambda: result_holder.append(manager.start("sid-1", config))
    )
    start_thread.start()
    assert started.wait(timeout=1)
    manager.push_audio(
        "sid-1",
        _speech(3200),
        16000,
        0,
    )
    release.set()
    start_thread.join(timeout=1)

    assert result_holder
    assert result_holder[0]["status"] == "starting"
    assert result_holder[0]["ready"] is False

    # The worker runs on its own thread. Wait for it to replay the queued audio
    # before stopping, otherwise this only passes on a lucky schedule.
    deadline = time.monotonic() + 2.0
    while not received and time.monotonic() < deadline:
        time.sleep(0.01)
    manager.stop("sid-1")
    assert received
    ready = [payload for event, payload in events if event == "transcription_ready"]
    assert ready and ready[0]["session_id"] == result_holder[0]["session_id"]


def test_provider_start_failure_is_emitted_and_session_can_be_stopped():
    emitted = []

    class FailingProvider(FakeProvider):
        def start(self, _config):
            raise ProviderError("MODEL_START_FAILED", "模型启动失败", "切换模型")

    manager = SessionManager(
        provider_factory=lambda _config: FailingProvider(),
        emit=lambda _sid, event, payload: emitted.append((event, payload)),
    )
    result = manager.start(
        "sid-1", SessionConfig("local", "fake", "en", 16000)
    )
    manager._sessions["sid-1"].worker.join(timeout=1)
    stopped = manager.stop("sid-1")

    assert result["status"] == "starting"
    assert any(
        event == "transcription_error" and payload["code"] == "MODEL_START_FAILED"
        for event, payload in emitted
    )
    assert stopped["status"] == "error"


def test_second_start_for_same_sid_is_rejected():
    manager = SessionManager(provider_factory=lambda _config: FakeProvider())
    config = SessionConfig("local", "fake", "en", 16000)
    manager.start("sid-1", config)
    with pytest.raises(ValueError, match="SESSION_ALREADY_ACTIVE"):
        manager.start("sid-1", config)
    manager.stop("sid-1")


def test_stop_is_idempotent_and_closes_provider():
    provider = FakeProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    manager.start("sid-1", SessionConfig("local", "fake", "en", 16000))
    manager.stop("sid-1")
    manager.stop("sid-1")
    assert provider.closed is True


def test_audio_queue_backpressure_drops_oldest_frame_without_failing_session():
    manager = SessionManager(provider_factory=lambda _config: FakeProvider())
    manager.start("sid-1", SessionConfig("local", "fake", "en", 16000, max_queue=1))
    state = manager._sessions["sid-1"]
    state.stop_event.set()
    state.worker.join(timeout=1)
    state.audio_queue = queue.Queue(maxsize=1)
    state.audio_queue.put_nowait("old-frame")

    dropped = manager.push_audio(
        "sid-1",
        _speech(160),
        sample_rate=16000,
        sequence=1,
    )

    assert dropped is True
    assert state.audio_queue.get_nowait().sequence == 1
    manager.stop("sid-1")


def test_contiguous_provider_receives_adjacent_windows_without_whisper_overlap():
    class ContiguousProvider(FakeProvider):
        requires_contiguous_audio = True

        def __init__(self):
            self.chunks = []

        def push(self, audio):
            self.chunks.append(audio.copy())
            return []

    provider = ContiguousProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    config = SessionConfig(
        "local",
        "fake",
        "en",
        16000,
        window_seconds=1.0,
        overlap_seconds=0.5,
    )
    manager.start("sid-1", config)
    manager.push_audio(
        "sid-1",
        _speech(24000),
        sample_rate=16000,
        sequence=1,
    )

    manager.stop("sid-1")

    assert [chunk.size for chunk in provider.chunks] == [16000, 8000]


def test_contiguous_provider_is_fed_in_streaming_chunks_not_window_chunks():
    """A streaming provider only needs delivery granularity, not whisper windows.

    Feeding it 3-second windows made the live draft jump once every 3 seconds,
    which reads as "the transcript cannot keep up with the speaker".
    """

    class ContiguousProvider(FakeProvider):
        requires_contiguous_audio = True

        def __init__(self):
            self.chunk_sizes = []

        def push(self, audio):
            self.chunk_sizes.append(audio.size)
            return []

    provider = ContiguousProvider()
    manager = SessionManager(
        provider_factory=lambda _config: provider,
        streaming_chunk_seconds=0.25,
    )
    config = SessionConfig(
        "local", "fake", "en", 16000, window_seconds=3.0, overlap_seconds=0.0
    )
    manager.start("sid-1", config)
    manager.push_audio("sid-1", _speech(16000), sample_rate=16000, sequence=1)
    manager.stop("sid-1")

    # 1s of audio through a 0.25s window becomes four pushes, not one 3s window.
    assert provider.chunk_sizes == [4000, 4000, 4000, 4000]


def test_adaptive_streaming_chunk_policy_only_grows_when_inference_falls_behind():
    policy = AdaptiveStreamingChunkPolicy(1.0)

    assert policy.observe(inference_seconds=0.35, queued_chunks=0) == 1.0
    assert policy.observe(inference_seconds=1.1, queued_chunks=4) == 1.25
    assert policy.observe(inference_seconds=1.1, queued_chunks=4) == 1.5
    assert policy.observe(inference_seconds=0.2, queued_chunks=0) == 1.25
    assert policy.observe(inference_seconds=0.2, queued_chunks=0) == 1.0


def test_windowed_provider_keeps_the_configured_window_size():
    class WindowedProvider(FakeProvider):
        requires_contiguous_audio = False

        def __init__(self):
            self.chunk_sizes = []

        def push(self, audio):
            self.chunk_sizes.append(audio.size)
            return []

    provider = WindowedProvider()
    manager = SessionManager(
        provider_factory=lambda _config: provider,
        streaming_chunk_seconds=0.25,
    )
    config = SessionConfig(
        "cloud", "fake", "en", 16000, window_seconds=3.0, overlap_seconds=0.0
    )
    manager.start("sid-1", config)
    manager.push_audio("sid-1", _speech(48000), sample_rate=16000, sequence=1)
    manager.stop("sid-1")

    # The streaming chunk size must not leak into the windowed (cloud/Whisper)
    # path: those providers really do infer once per window. Three seconds of
    # audio through a 0.25s streaming chunk would otherwise be 12 pushes.
    assert provider.chunk_sizes == [48000]


def test_provider_starts_and_pushes_on_the_same_worker_thread():
    class ThreadAffineProvider(FakeProvider):
        def start(self, config):
            self.thread_id = threading.get_ident()

        def push(self, _audio):
            assert threading.get_ident() == self.thread_id
            return []

    provider = ThreadAffineProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    config = SessionConfig(
        "local",
        "fake",
        "en",
        16000,
        window_seconds=0.2,
        overlap_seconds=0.0,
    )

    manager.start("sid-1", config)
    manager.push_audio(
        "sid-1",
        _speech(3200),
        sample_rate=16000,
        sequence=1,
    )

    result = manager.stop("sid-1")

    assert result["status"] == "success"


def test_contiguous_provider_timestamps_are_not_offset_twice():
    class AbsoluteTimestampProvider(FakeProvider):
        requires_contiguous_audio = True

        def __init__(self):
            self.calls = 0

        def push(self, _audio):
            self.calls += 1
            if self.calls == 2:
                return [TranscriptSegment("absolute-1", "confirmed", 1000, 1500, True)]
            return []

    provider = AbsoluteTimestampProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    config = SessionConfig(
        "local", "fake", "en", 16000, window_seconds=1.0, overlap_seconds=0.5
    )
    manager.start("sid-1", config)
    manager.push_audio(
        "sid-1",
        _speech(32000),
        sample_rate=16000,
        sequence=1,
    )

    result = manager.stop("sid-1")

    assert result["segments"][0]["start_ms"] == 1000


def test_provisional_segments_are_emitted_but_not_saved_as_history():
    emitted = []

    class ProvisionalProvider(FakeProvider):
        def push(self, _audio):
            return [
                TranscriptSegment("draft-1", "draft", 0, 500, False),
                TranscriptSegment("final-1", "confirmed", 500, 1000, True),
            ]

    provider = ProvisionalProvider()
    manager = SessionManager(
        provider_factory=lambda _config: provider,
        emit=lambda _sid, event, payload: emitted.append((event, payload)),
    )
    config = SessionConfig(
        "local", "fake", "en", 16000, window_seconds=0.2, overlap_seconds=0.0
    )
    manager.start("sid-1", config)
    manager.push_audio(
        "sid-1",
        _speech(3200),
        sample_rate=16000,
        sequence=1,
    )

    result = manager.stop("sid-1")

    emitted_segments = [payload for event, payload in emitted if event == "transcript_segment"]
    assert {item["id"] for item in emitted_segments} == {"draft-1", "final-1"}
    assert [item["id"] for item in result["segments"]] == ["final-1"]


def test_stop_admits_the_worker_is_still_loading_the_model():
    """Stopping during model load must not report a clean shutdown.

    Claiming success hides the fact that nothing was ever transcribed, and the
    worker used to announce itself ready long after the class had ended.
    """
    emitted = []

    class SlowProvider(FakeProvider):
        def start(self, config):
            time.sleep(0.5)

    manager = SessionManager(
        provider_factory=lambda _config: SlowProvider(),
        emit=lambda _sid, event, _payload: emitted.append(event),
    )
    config = SessionConfig("local", "fake", "en", 16000, stop_timeout_seconds=0.05)
    manager.start("sid-1", config)

    result = manager.stop("sid-1")

    assert result["status"] == "stopping"
    assert result["detail"]
    # Once the model finally loads, the dead session must stay silent.
    time.sleep(0.9)
    assert emitted == []


def test_a_model_that_never_becomes_ready_is_reported_as_a_timeout():
    """The UI must not wait forever on a model that will not load.

    The worker blocks inside provider startup, so the timeout is measured
    against arriving audio instead of from inside the worker.
    """
    emitted = []

    class HangingProvider(FakeProvider):
        def start(self, config):
            time.sleep(0.5)

    manager = SessionManager(
        provider_factory=lambda _config: HangingProvider(),
        emit=lambda _sid, event, payload: emitted.append((event, payload)),
        startup_timeout_seconds=0.05,
    )
    manager.start("sid-1", SessionConfig("local", "fake", "en", 16000))

    time.sleep(0.15)
    manager.push_audio("sid-1", _speech(3200), 16000, 1)

    errors = [payload for event, payload in emitted if event == "transcription_error"]
    assert errors, "a model that never loads must be reported"
    assert errors[0]["code"] == "PROVIDER_START_TIMEOUT"
    # Reported once, not on every chunk.
    manager.push_audio("sid-1", _speech(3200), 16000, 2)
    assert len([p for e, p in emitted if e == "transcription_error"]) == 1
    manager.stop("sid-1")


def test_restarting_after_a_slow_stop_stays_usable():
    """Stop while the model loads, start again: the app must recover."""
    class SlowProvider(FakeProvider):
        def start(self, config):
            time.sleep(0.3)

    manager = SessionManager(provider_factory=lambda _config: SlowProvider())
    config = SessionConfig("local", "fake", "en", 16000, stop_timeout_seconds=0.05)

    manager.start("sid-1", config)
    assert manager.stop("sid-1")["status"] == "stopping"

    assert manager.start("sid-1", config)["status"] == "starting"
    time.sleep(0.5)
    assert manager.stop("sid-1")["status"] == "success"


def test_capture_offset_places_segments_on_the_recording_timeline():
    class OffsetProvider(FakeProvider):
        def __init__(self):
            self.calls = 0

        def push(self, _audio):
            self.calls += 1
            if self.calls == 2:
                return [TranscriptSegment("offset-1", "late start", 1000, 1500, True)]
            return []

    provider = OffsetProvider()
    manager = SessionManager(provider_factory=lambda _config: provider)
    config = SessionConfig(
        "local", "fake", "en", 16000, window_seconds=1.0, overlap_seconds=0.5
    )
    manager.start("sid-1", config)
    manager.push_audio(
        "sid-1",
        _speech(32000),
        sample_rate=16000,
        sequence=1,
        offset_ms=8000,
    )

    result = manager.stop("sid-1")

    # The recording clock started 8s before the model accepted its first chunk.
    # The second window starts 500ms into the 1s/0.5s sliding timeline, so it
    # lands at 8s + 500ms + 1000ms of provider-reported offset.
    assert result["segments"][0]["start_ms"] == 9500
    assert result["segments"][0]["end_ms"] == 10000


def test_dropped_audio_keeps_recording_timeline_aligned():
    holding = threading.Event()

    class HoldingProvider(FakeProvider):
        name = "holding"
        model = "holding-model"

        def push(self, _audio):
            # Keep the consumer busy so the queue really backs up.
            assert holding.wait(timeout=2)
            return []

    manager = SessionManager(provider_factory=lambda _config: HoldingProvider())
    manager.start(
        "sid-1",
        SessionConfig(
            "local", "fake", "en", 16000, window_seconds=1.0, overlap_seconds=0.5, max_queue=2
        ),
    )
    # The first window blocks inside the provider, later windows fill the queue,
    # and the window after that makes the queue drop the oldest second of audio.
    dropped = False
    for sequence in range(1, 6):
        dropped = manager.push_audio(
            "sid-1",
            _speech(16000),
            16000,
            sequence,
            offset_ms=(sequence - 1) * 1000,
        )
        if dropped:
            break
    assert dropped is True

    state = manager._sessions["sid-1"]
    with manager._lock:
        timeline = state.timeline
    assert timeline.origin_ms == 0
    assert timeline.dropped_ms == 1000

    holding.set()
    manager.stop("sid-1")

    # The dropped second still occupies recording time, so the provider's own
    # one-second position must be reported two seconds into the recording.
    assert timeline.absolute_ms(1000) == 2000


def test_capture_offset_skips_audio_dropped_before_the_model_started():
    manager = SessionManager(provider_factory=lambda _config: FakeProvider())
    manager.start("sid-1", SessionConfig("local", "fake", "en", 16000))
    state = manager._sessions["sid-1"]
    state.stop_event.set()
    state.worker.join(timeout=1)

    # The browser discarded the pre-model backlog, so the first chunk the model
    # receives is already 12s into the recording.
    manager.push_audio(
        "sid-1",
        _speech(16000),
        16000,
        sequence=1,
        offset_ms=12000,
    )

    with manager._lock:
        timeline = state.timeline
    assert timeline.origin_known is True
    assert timeline.origin_ms == 12000
    assert timeline.absolute_ms(0) == 12000
    assert timeline.absolute_ms(2500) == 14500
    manager.stop("sid-1")
