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
    assert start["status"] == "success"
    stop = client.emit("stop_transcription", {}, callback=True)
    assert stop["status"] == "success"
    client.disconnect()
