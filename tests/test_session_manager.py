import base64
import queue

import pytest

from backend.models import SessionConfig
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
