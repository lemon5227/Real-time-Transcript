import builtins
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

    def fake_hf_hub_download(repo_id, filename, cache_dir, tqdm_class=None):
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

    def fake_hf_hub_download(repo_id, filename, cache_dir, tqdm_class=None):
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


def _drive_huggingface_bar(bar_class, total_bytes, chunk_bytes):
    """Build a bar the way `huggingface_hub` does and stream a download through it.

    The library constructs the class with `desc`/`total`/`unit`/`unit_scale` and then calls
    `update(len(chunk))` per chunk inside a `with` block. Reproducing that call shape is the
    point: a stub that calls the class differently would pass against code that cannot work.
    """
    delivered = 0
    bar = bar_class(desc="model.safetensors", total=total_bytes, unit="B", unit_scale=True)
    with bar:
        while delivered < total_bytes:
            n = min(chunk_bytes, total_bytes - delivered)
            bar.update(n)
            delivered += n
    return delivered


def test_mlx_weight_download_reports_progress_while_bytes_arrive(tmp_path, monkeypatch):
    """The 5% stall: the weight download has to publish progress as it runs.

    `hf_hub_download` offers no progress callback, so `ModelManager` passes it a `tqdm`
    subclass and reads bytes off it. Before that, the only states ever written between "5%"
    and "100%" were the two ends of the download, and a first-run user watched a dead bar
    for the minutes it takes to pull Parakeet.
    """
    weights_path = tmp_path / "model.safetensors"
    total_bytes = 100 * 1024 * 1024
    seen_progress = []

    def fake_hf_hub_download(repo_id, filename, cache_dir, tqdm_class=None):
        if filename == "config.json":
            return str(tmp_path / "config.json")
        assert tqdm_class is not None, "the weight download must be given a progress sink"
        _drive_huggingface_bar(tqdm_class, total_bytes, 10 * 1024 * 1024)
        weights_path.write_bytes(b"x" * 1024)
        return str(weights_path)

    fake_huggingface = SimpleNamespace(
        hf_hub_download=fake_hf_hub_download,
        try_to_load_from_cache=lambda *_a, **_k: None,
    )
    original_import = __import__("backend.model_manager", fromlist=["importlib"]).importlib.import_module
    monkeypatch.setattr(
        "backend.model_manager.importlib.import_module",
        lambda name: fake_huggingface if name == "huggingface_hub" else original_import(name),
    )
    manager = ModelManager(
        ({"id": "parakeet", "label": "Parakeet", "runtime": "mlx", "model_ref": "mlx-community/p"},),
        dependency_checker=lambda _model: True,
        runtime_cache_root=tmp_path,
    )
    real_advance = manager._advance_download_state

    def recording_advance(model_id, **changes):
        real_advance(model_id, **changes)
        if "progress" in changes:
            seen_progress.append(int(changes["progress"]))

    monkeypatch.setattr(manager, "_advance_download_state", recording_advance)

    manager._download_runtime_model("parakeet", threading.Event())

    assert len(seen_progress) > 5, "progress published only at the ends: %s" % seen_progress
    assert seen_progress == sorted(seen_progress), "progress went backwards: %s" % seen_progress
    assert 5 in seen_progress, "the download should start at the 5% marker"
    assert max(seen_progress) < 100, "100%% is reserved for the verified file"
    assert manager.get_model("parakeet")["status"] == "ready"


def test_mlx_progress_sink_counts_bytes_even_with_the_bar_disabled(tmp_path, monkeypatch, capsys):
    """A disabled tqdm bar drops `update()` on the floor, so the sink cannot trust `n`.

    tqdm returns early from `update()` when `disable` is set, leaving `n` frozen at
    `initial`. The sink keeps its own byte tally for exactly that reason, and forces the bar
    off because Hugging Face only injects `disable` for *its own* subclass — inheriting a
    live terminal bar would stream megabytes of progress output into the DMG's stderr log.
    """
    weights_path = tmp_path / "model.safetensors"
    total_bytes = 50 * 1024 * 1024
    published = []

    def fake_hf_hub_download(repo_id, filename, cache_dir, tqdm_class=None):
        if filename == "config.json":
            return str(tmp_path / "config.json")
        assert tqdm_class is not None, "the weight download must be given a progress sink"
        _drive_huggingface_bar(tqdm_class, total_bytes, 5 * 1024 * 1024)
        weights_path.write_bytes(b"x" * 1024)
        return str(weights_path)

    fake_huggingface = SimpleNamespace(
        hf_hub_download=fake_hf_hub_download,
        try_to_load_from_cache=lambda *_a, **_k: None,
    )
    original_import = __import__("backend.model_manager", fromlist=["importlib"]).importlib.import_module
    monkeypatch.setattr(
        "backend.model_manager.importlib.import_module",
        lambda name: fake_huggingface if name == "huggingface_hub" else original_import(name),
    )
    manager = ModelManager(
        ({"id": "parakeet", "label": "Parakeet", "runtime": "mlx", "model_ref": "mlx-community/p"},),
        dependency_checker=lambda _model: True,
        runtime_cache_root=tmp_path,
    )
    real_advance = manager._advance_download_state

    def recording_advance(model_id, **changes):
        real_advance(model_id, **changes)
        if "downloaded_bytes" in changes:
            published.append(int(changes["downloaded_bytes"]))

    monkeypatch.setattr(manager, "_advance_download_state", recording_advance)

    manager._download_runtime_model("parakeet", threading.Event())

    assert published, "the sink published no byte counts"
    assert max(published) == total_bytes, "byte tally stopped early: %s" % max(published)
    assert manager.get_model("parakeet")["status"] == "ready"
    captured = capsys.readouterr()
    assert "model.safetensors" not in captured.err, "the bar printed to stderr instead of staying silent"


def test_mlx_progress_sink_survives_a_missing_tqdm(tmp_path, monkeypatch):
    """No tqdm means no byte reports, but the download must still finish.

    `huggingface_hub` requires tqdm, so this cannot happen on a machine that really
    downloads weights. It is covered because the alternative — raising — would be caught by
    the download worker's blanket `except Exception` and surfaced to the user as "download
    failed, check your network" for a missing package.
    """
    real_import = builtins.__import__

    def import_without_tqdm(name, *args, **kwargs):
        if name == "tqdm":
            raise ImportError("no tqdm")
        return real_import(name, *args, **kwargs)

    manager = ModelManager(
        ({"id": "parakeet", "label": "Parakeet", "runtime": "mlx", "model_ref": "mlx-community/p"},),
        dependency_checker=lambda _model: True,
        runtime_cache_root=tmp_path,
    )
    monkeypatch.setattr("builtins.__import__", import_without_tqdm)

    assert manager._byte_progress_sink("parakeet") is None


def test_advance_download_state_never_moves_progress_backwards():
    """Two bars, one model: Xet-backed downloads report network and disk separately.

    `huggingface_hub`'s Xet path builds two bars from the same `tqdm_class` — one counting
    bytes over the wire, one counting bytes written — so two sinks publish the same model at
    different rates. Without the merge rule in `_advance_download_state` the UI progress bar
    flaps back and forth between them.
    """
    manager = ModelManager(
        ({"id": "parakeet", "label": "Parakeet", "runtime": "mlx", "model_ref": "mlx-community/p"},),
        dependency_checker=lambda _model: True,
    )
    manager._set_state("parakeet", status="downloading", progress=0, downloaded_bytes=0)

    manager._advance_download_state("parakeet", downloaded_bytes=70_000_000, total_bytes=100_000_000, progress=71)
    manager._advance_download_state("parakeet", downloaded_bytes=30_000_000, total_bytes=100_000_000, progress=33)

    state = manager.get_model("parakeet")
    assert state["progress"] == 71, "the lagging second bar dragged progress backwards"
    assert state["downloaded_bytes"] == 70_000_000, "the byte counter rewound"
    # Non-progress fields still update normally.
    manager._advance_download_state("parakeet", message="正在校验文件")
    assert manager.get_model("parakeet")["message"] == "正在校验文件"
