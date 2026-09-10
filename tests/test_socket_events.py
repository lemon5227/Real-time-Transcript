import base64
import threading
import time

from backend import create_app, socketio
from backend.models import TranscriptSegment


def wait_for_event(client, name, timeout=2.0):
    """Collect socket events until the worker thread has emitted ``name``.

    ``transcription_ready`` is emitted from the transcription worker, so reading
    the client buffer once is a race.
    """
    deadline = time.monotonic() + timeout
    received = []
    while True:
        received.extend(client.get_received())
        if any(item["name"] == name for item in received):
            return received
        if time.monotonic() >= deadline:
            return received
        time.sleep(0.01)


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
    received = wait_for_event(client, "transcription_ready")
    assert any(item["name"] == "transcription_session_created" for item in received)
    assert any(item["name"] == "transcription_ready" for item in received)
    stopped = client.emit("stop_transcription", {}, callback=True)
    assert stopped["status"] == "success"
    client.disconnect()


def test_socket_applies_the_course_glossary_to_transcribed_text():
    class MisspellingProvider(FakeProvider):
        def push(self, _audio):
            return [TranscriptSegment("1", "today we cover gradien descent", 0, 1000, True)]

    app = create_app({}, provider_factory=lambda _config: MisspellingProvider())
    client = socketio.test_client(app)
    client.emit(
        "start_transcription",
        {"mode": "local", "model": "fake", "language": "en", "glossary": "gradient descent"},
        callback=True,
    )
    client.get_received()  # drain the start acknowledgement events

    accepted = client.emit(
        "audio_chunk",
        {
            # The default 3s window needs a full window before the provider runs.
            "audio": base64.b64encode(b"\x00\x00" * 48000).decode(),
            "sample_rate": 16000,
            "sequence": 0,
        },
        callback=True,
    )
    assert accepted["status"] == "accepted"

    received = wait_for_event(client, "transcript_segment")
    segments = [
        item["args"][0] for item in received if item["name"] == "transcript_segment"
    ]
    assert segments, "the glossary session must still emit transcript segments"
    assert segments[0]["text"] == "today we cover gradient descent"
    client.disconnect()
