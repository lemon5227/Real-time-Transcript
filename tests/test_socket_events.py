import base64
import threading

from backend import create_app, socketio
from backend.models import TranscriptSegment


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def start(self, _config):
        return None

    def push(self, _audio):
        return [TranscriptSegment("1", "lecture text", 0, 1000, True, 0.9)]

    def flush(self):
        return []

    def close(self):
        return None


def test_socket_start_and_stop_return_ack():
    app = create_app({}, provider_factory=lambda _config: FakeProvider())
    client = socketio.test_client(app)
    start = client.emit(
        "start_transcription",
        {"mode": "local", "model": "fake", "language": "en", "sample_rate": 48000},
        callback=True,
    )
    assert start["status"] == "starting"
    assert start["ready"] is False
    stop = client.emit("stop_transcription", {}, callback=True)
    assert stop["status"] == "success"
    client.disconnect()


def test_socket_accepts_audio_while_provider_is_starting():
    started = threading.Event()
    release = threading.Event()

    class DelayedProvider(FakeProvider):
        def start(self, _config):
            started.set()
            assert release.wait(timeout=1)

    app = create_app({}, provider_factory=lambda _config: DelayedProvider())
    client = socketio.test_client(app)
    result_holder = []
    start_thread = threading.Thread(
        target=lambda: result_holder.append(
            client.emit(
                "start_transcription",
                {"mode": "local", "model": "fake", "language": "en"},
                callback=True,
            )
        )
    )
    start_thread.start()

    assert started.wait(timeout=1)
    accepted = client.emit(
        "audio_chunk",
        {
            "audio": base64.b64encode(b"\x00\x00" * 160).decode(),
            "sample_rate": 16000,
            "sequence": 0,
        },
        callback=True,
    )
    assert accepted["status"] == "accepted"
    release.set()
    start_thread.join(timeout=1)

    assert result_holder and result_holder[0]["status"] == "starting"
    received = client.get_received()
    assert any(item["name"] == "transcription_session_created" for item in received)
    assert any(item["name"] == "transcription_ready" for item in received)
    stopped = client.emit("stop_transcription", {}, callback=True)
    assert stopped["status"] == "success"
    client.disconnect()
