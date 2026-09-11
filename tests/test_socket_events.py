import base64
import threading
import time

from backend import create_app, socketio
from backend.models import TranscriptSegment


def _speech(frames):
    """Base64 PCM16 of audible speech at about -12 dBFS.

    The voice gate now drops near-silent windows before the provider sees them,
    so tests that exercise transcription have to push audio with real level
    instead of the digital silence these used to send.
    """
    return base64.b64encode(b"\x40\x1f" * frames).decode()  # 8000 as int16 LE


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
            "audio": _speech(160),
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
            "audio": _speech(48000),
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


def test_socket_refuses_origins_outside_the_configured_policy():
    """Any web page could otherwise drive the local server over the socket.

    Starting transcription, downloading models and calling translation all spend
    local resources or paid quota, so the socket has to follow the same origin
    policy the HTTP API already enforces.
    """
    app = create_app({"CORS_ORIGINS": "https://class.example.com"})
    client = app.test_client()
    try:
        allowed = client.get(
            "/socket.io/?EIO=4&transport=polling",
            headers={"Origin": "https://class.example.com"},
        )
        refused = client.get(
            "/socket.io/?EIO=4&transport=polling",
            headers={"Origin": "https://elsewhere.test"},
        )
        assert allowed.status_code == 200
        assert refused.status_code == 400
    finally:
        create_app({})  # restore the default policy on the shared socket
