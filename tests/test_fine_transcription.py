import time

from backend.fine_transcription import FineTranscriptionManager


class FakeSentence:
    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class FakeResult:
    sentences = [FakeSentence("Hello class.", 1.2, 2.8)]


class FakeBatchModel:
    def transcribe(self, path, **kwargs):
        assert path
        assert kwargs["chunk_duration"] == 600.0
        assert kwargs["overlap_duration"] == 15.0
        callback = kwargs.get("chunk_callback")
        if callback:
            callback(2.8, 2.8)
        return FakeResult()


class FakeCache:
    def __init__(self, model):
        self.model = model
        self.acquired = []
        self.released = []

    def acquire(self, model_ref):
        self.acquired.append(model_ref)
        if isinstance(self.model, Exception):
            raise self.model
        return self.model

    def release(self, model_ref):
        self.released.append(model_ref)


def wait_until(read_job, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = read_job()
        if job and job["status"] in {"ready", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("fine transcription job did not finish")


def test_fine_transcription_job_returns_timestamped_segments():
    cache = FakeCache(FakeBatchModel())
    manager = FineTranscriptionManager(model_cache=cache)

    created = manager.start(b"audio", ".webm", "en", "mlx-community/parakeet-tdt-0.6b-v3")
    result = wait_until(lambda: manager.get(created["job_id"]))

    assert result["status"] == "ready"
    assert result["segments"] == [
        {
            "id": "refined-0",
            "text": "Hello class.",
            "startMs": 1200,
            "endMs": 2800,
            "isFinal": True,
        }
    ]
    assert result["model"] == "mlx-community/parakeet-tdt-0.6b-v3"
    assert result["language"] == "en"
    assert cache.released == ["mlx-community/parakeet-tdt-0.6b-v3"]


def test_fine_transcription_job_records_actionable_failure():
    manager = FineTranscriptionManager(model_cache=FakeCache(RuntimeError("missing mlx")))

    created = manager.start(b"audio", ".webm", "en", "mlx-community/parakeet-tdt-0.6b-v3")
    result = wait_until(lambda: manager.get(created["job_id"]))

    assert result["status"] == "failed"
    assert result["error"]["code"] == "FINE_TRANSCRIPTION_FAILED"
    assert result["error"]["action"]
