import base64
import queue
import threading

import pytest

from backend.models import SessionConfig, TranscriptSegment
from backend.providers.base import ProviderError
from backend.session_manager import SessionManager


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
        base64.b64encode(b"\x00\x00" * 3200).decode(),
        16000,
        0,
    )
    release.set()
    start_thread.join(timeout=1)

    assert result_holder
    assert result_holder[0]["status"] == "starting"
    assert result_holder[0]["ready"] is False
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
        base64.b64encode(b"\x00\x00" * 160).decode(),
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
        base64.b64encode(b"\x00\x00" * 24000).decode(),
        sample_rate=16000,
        sequence=1,
    )

    manager.stop("sid-1")

    assert [chunk.size for chunk in provider.chunks] == [16000, 8000]


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
        base64.b64encode(b"\x00\x00" * 3200).decode(),
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
        base64.b64encode(b"\x00\x00" * 32000).decode(),
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
        base64.b64encode(b"\x00\x00" * 3200).decode(),
        sample_rate=16000,
        sequence=1,
    )

    result = manager.stop("sid-1")

    emitted_segments = [payload for event, payload in emitted if event == "transcript_segment"]
    assert {item["id"] for item in emitted_segments} == {"draft-1", "final-1"}
    assert [item["id"] for item in result["segments"]] == ["final-1"]
