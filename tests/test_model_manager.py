import threading

from backend.model_manager import ModelManager


def test_model_manager_reports_cached_weights(tmp_path):
    catalog = (
        {"id": "tiny", "label": "Tiny", "size": "~75MB", "best_for": "低配 CPU"},
    )
    (tmp_path / "tiny.pt").write_bytes(b"ready")
    manager = ModelManager(
        catalog, dependency_checker=lambda _model: True, cache_root=tmp_path
    )

    model = manager.list_models()[0]

    assert model["status"] == "ready"
    assert model["weights_available"] is True
    assert model["downloaded_bytes"] == 5


def test_model_manager_starts_one_async_download(tmp_path, monkeypatch):
    catalog = (
        {
            "id": "tiny",
            "label": "Tiny",
            "size": "~75MB",
            "best_for": "低配 CPU",
            "url": "https://example.test/tiny.pt",
        },
    )

    class Response:
        def info(self):
            return {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            if getattr(self, "read_once", False):
                return b""
            self.read_once = True
            return b"data"

    monkeypatch.setattr(
        "backend.model_manager.urllib.request.urlopen", lambda _url, **_kwargs: Response()
    )
    manager = ModelManager(
        catalog, dependency_checker=lambda _model: True, cache_root=tmp_path
    )

    result = manager.start_download("tiny")

    assert result["status"] in {"downloading", "ready"}
    assert manager.wait_for("tiny", timeout=1)["status"] == "ready"
    assert (tmp_path / "tiny.pt").read_bytes() == b"data"
    assert not (tmp_path / "tiny.pt.part").exists()


def test_model_manager_rejects_duplicate_download(tmp_path, monkeypatch):
    catalog = (
        {
            "id": "tiny",
            "label": "Tiny",
            "size": "~75MB",
            "best_for": "低配 CPU",
            "url": "https://example.test/tiny.pt",
        },
    )

    started = threading.Event()
    release = threading.Event()

    class SlowResponse:
        def info(self):
            return {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            started.set()
            release.wait(1)
            return b""

    monkeypatch.setattr(
        "backend.model_manager.urllib.request.urlopen",
        lambda _url, **_kwargs: SlowResponse(),
    )
    manager = ModelManager(
        catalog, dependency_checker=lambda _model: True, cache_root=tmp_path
    )
    first = manager.start_download("tiny")
    assert started.wait(1)
    second = manager.start_download("tiny")

    assert first["status"] == "downloading"
    assert second["status"] == "downloading"
    manager.cancel_download("tiny")
    release.set()
    manager.wait_for("tiny", timeout=1)
