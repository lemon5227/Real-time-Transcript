import threading
from types import SimpleNamespace

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
    assert model["runtime"] == "standard"


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


def test_model_manager_can_pre_download_mlx_runtime_model(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    weights_path = tmp_path / "model.safetensors"
    config_path.write_text("{}", encoding="utf-8")
    weights_path.write_bytes(b"mlx-weights")
    downloaded = set()

    def fake_hf_hub_download(repo_id, filename, cache_dir):
        assert repo_id == "mlx-community/test-parakeet"
        assert cache_dir == str(tmp_path)
        downloaded.add(filename)
        return str(config_path if filename == "config.json" else weights_path)

    def fake_try_to_load_from_cache(repo_id, filename, cache_dir):
        if repo_id == "mlx-community/test-parakeet" and filename in downloaded:
            return str(config_path if filename == "config.json" else weights_path)
        return None

    fake_huggingface = SimpleNamespace(
        hf_hub_download=fake_hf_hub_download,
        try_to_load_from_cache=fake_try_to_load_from_cache,
    )
    original_import = __import__("backend.model_manager", fromlist=["importlib"]).importlib.import_module

    def fake_import(name):
        if name == "huggingface_hub":
            return fake_huggingface
        return original_import(name)

    monkeypatch.setattr("backend.model_manager.importlib.import_module", fake_import)
    catalog = (
        {
            "id": "parakeet",
            "label": "Parakeet",
            "runtime": "mlx",
            "model_ref": "mlx-community/test-parakeet",
            "size": "~1GB",
        },
    )
    manager = ModelManager(
        catalog,
        dependency_checker=lambda _model: True,
        runtime_cache_root=tmp_path,
    )

    result = manager.start_download("parakeet")

    assert result["status"] in {"downloading", "ready"}
    model = manager.wait_for("parakeet", timeout=1)
    assert model["status"] == "ready"
    assert model["download_supported"] is True
    assert model["weights_available"] is True
    assert model["downloaded_bytes"] == len(b"mlx-weights")


def test_model_manager_can_download_mlx_weights_before_runtime_install(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    weights_path = tmp_path / "model.safetensors"
    downloaded = set()

    def fake_hf_hub_download(repo_id, filename, cache_dir):
        assert repo_id == "mlx-community/test-parakeet"
        assert cache_dir == str(tmp_path)
        downloaded.add(filename)
        path = config_path if filename == "config.json" else weights_path
        if filename == "config.json":
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_bytes(b"mlx-weights")
        return str(path)

    def fake_try_to_load_from_cache(repo_id, filename, cache_dir):
        if repo_id == "mlx-community/test-parakeet" and filename in downloaded:
            return str(config_path if filename == "config.json" else weights_path)
        return None

    fake_huggingface = SimpleNamespace(
        hf_hub_download=fake_hf_hub_download,
        try_to_load_from_cache=fake_try_to_load_from_cache,
    )
    original_import = __import__("backend.model_manager", fromlist=["importlib"]).importlib.import_module

    def fake_import(name):
        if name == "huggingface_hub":
            return fake_huggingface
        return original_import(name)

    monkeypatch.setattr("backend.model_manager.importlib.import_module", fake_import)
    manager = ModelManager(
        (
            {
                "id": "parakeet",
                "label": "Parakeet",
                "runtime": "mlx",
                "model_ref": "mlx-community/test-parakeet",
            },
        ),
        dependency_checker=lambda _model: False,
        runtime_cache_root=tmp_path,
    )

    assert manager.list_models()[0]["status"] == "dependency_missing"
    result = manager.start_download("parakeet")

    assert result["status"] in {"downloading", "dependency_missing"}
    model = manager.wait_for("parakeet", timeout=1)
    assert model["weights_available"] is True
    assert model["status"] == "dependency_missing"
