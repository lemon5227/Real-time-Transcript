"""Manage the FluidAudio Nemotron speaker model from the settings page.

The live session treats diarization as optional: when the weights are missing
the helper downloads them on first use, which can stall the start of a lecture.
These tests cover the explicit "download ahead of class" path and the disk
inspection behind the model-library card.
"""

import threading
from pathlib import Path

import pytest

from backend import create_app
from backend.diarization_models import (
    DiarizationModelError,
    DiarizationModelManager,
    default_model_cache_root,
)
from backend.providers.diarization import DiarizationPrepareError

MODEL_FOLDER = "nemotron-3-diarization"


def seed_model_cache(root: Path, *, variant: str = "low") -> Path:
    """Recreate the FluidAudio cache layout that marks a preset as usable."""
    directory = root / MODEL_FOLDER
    bundle = directory / "monolithic" / f"Nemotron3Diarizer_{variant}.mlmodelc"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "coremldata.bin").write_bytes(b"compiled-weights")
    (directory / "learnable_sil_emb.bin").write_bytes(b"embedding")
    return directory


def test_snapshot_reports_not_downloaded_until_the_cache_is_complete(tmp_path):
    manager = DiarizationModelManager(command="helper", cache_root=tmp_path)
    empty = manager.snapshot()
    assert empty["status"] == "not_downloaded"
    assert empty["present"] is False
    assert empty["component_available"] is True
    assert empty["variant"] == "low"

    # A half-written cache (weights still streaming) must not read as ready.
    partial = tmp_path / MODEL_FOLDER / "monolithic" / "Nemotron3Diarizer_low.mlmodelc"
    partial.mkdir(parents=True)
    (partial / "weights.bin").write_bytes(b"partial")
    assert manager.snapshot()["status"] == "not_downloaded"

    seed_model_cache(tmp_path)
    ready = manager.snapshot()
    assert ready["status"] == "ready"
    assert ready["present"] is True
    assert ready["progress"] == 100
    assert ready["downloaded_bytes"] > 0
    assert ready["directory"] == str(tmp_path / MODEL_FOLDER)


def test_download_streams_progress_and_finishes_ready(tmp_path):
    calls = []
    started = threading.Event()
    release = threading.Event()

    def fake_prepare(command, *, variant, on_progress):
        calls.append((command, variant))
        on_progress(0.4)
        started.set()
        release.wait(timeout=5)
        seed_model_cache(tmp_path, variant=variant)

    manager = DiarizationModelManager(
        command="/opt/helper", variant="low", cache_root=tmp_path, prepare=fake_prepare
    )
    manager.start_download()
    assert started.wait(timeout=5)

    running = manager.snapshot()
    assert running["status"] == "downloading"
    assert running["progress"] == 40

    release.set()
    manager.wait_for_idle(timeout=5)

    ready = manager.snapshot()
    assert ready["status"] == "ready"
    assert ready["progress"] == 100
    assert calls == [("/opt/helper", "low")]


def test_download_failure_is_reported_and_can_be_retried(tmp_path):
    attempts = []

    def flaky_prepare(command, *, variant, on_progress):
        attempts.append(1)
        if len(attempts) == 1:
            raise DiarizationPrepareError("网络中断")
        on_progress(1.0)
        seed_model_cache(tmp_path, variant=variant)

    manager = DiarizationModelManager(command="helper", cache_root=tmp_path, prepare=flaky_prepare)
    manager.start_download()
    manager.wait_for_idle(timeout=5)

    failed = manager.snapshot()
    assert failed["status"] == "failed"
    assert failed["error"] == "网络中断"

    manager.start_download()
    manager.wait_for_idle(timeout=5)
    assert manager.snapshot()["status"] == "ready"


def test_download_requires_the_native_component(tmp_path):
    manager = DiarizationModelManager(command="", cache_root=tmp_path)
    with pytest.raises(DiarizationModelError) as excinfo:
        manager.start_download()
    assert excinfo.value.code == "component_missing"


def test_delete_clears_the_cached_weights(tmp_path):
    directory = seed_model_cache(tmp_path)
    manager = DiarizationModelManager(command="helper", cache_root=tmp_path)
    assert manager.snapshot()["status"] == "ready"

    result = manager.delete()

    assert not directory.exists()
    assert result["status"] == "not_downloaded"
    assert result["present"] is False
    assert result["downloaded_bytes"] == 0


def test_delete_is_refused_while_a_download_is_running(tmp_path):
    release = threading.Event()

    def blocking_prepare(command, *, variant, on_progress):
        release.wait(timeout=5)

    manager = DiarizationModelManager(
        command="helper", cache_root=tmp_path, prepare=blocking_prepare
    )
    manager.start_download()

    with pytest.raises(DiarizationModelError) as excinfo:
        manager.delete()
    assert excinfo.value.code == "busy"

    release.set()
    manager.wait_for_idle(timeout=5)


def test_cache_root_follows_the_launcher_environment():
    base = "/tmp/fluid-audio-models"
    assert default_model_cache_root({"NEMOTRON_MODEL_DIR": base}) == Path(base)
    assert default_model_cache_root({"NEMOTRON_MODEL_DIR": f"{base}/{MODEL_FOLDER}"}) == Path(base)
    assert default_model_cache_root({}) == (
        Path.home() / "Library" / "Application Support" / "FluidAudio" / "Models"
    )


def test_diarization_model_routes_download_and_delete(tmp_path):
    release = threading.Event()

    def fake_prepare(command, *, variant, on_progress):
        on_progress(0.5)
        release.wait(timeout=5)
        seed_model_cache(tmp_path, variant=variant)

    app = create_app({})
    manager = DiarizationModelManager(command="helper", cache_root=tmp_path, prepare=fake_prepare)
    app.extensions["diarization_models"] = manager
    client = app.test_client()

    body = client.get("/api/diarization/model").get_json()["model"]
    assert body["status"] == "not_downloaded"

    response = client.post("/api/diarization/model/download")
    assert response.status_code == 202
    assert response.get_json()["model"]["status"] == "downloading"

    release.set()
    manager.wait_for_idle(timeout=5)
    assert client.get("/api/diarization/model").get_json()["model"]["status"] == "ready"

    response = client.delete("/api/diarization/model")
    assert response.status_code == 200
    assert response.get_json()["model"]["status"] == "not_downloaded"


def test_diarization_download_conflicts_without_the_native_component(tmp_path):
    app = create_app({})
    app.extensions["diarization_models"] = DiarizationModelManager(command="", cache_root=tmp_path)
    response = app.test_client().post("/api/diarization/model/download")

    assert response.status_code == 409
    body = response.get_json()
    assert body["model"]["component_available"] is False
    assert body["error"]


def test_diarization_delete_conflicts_while_downloading(tmp_path):
    release = threading.Event()

    def blocking_prepare(command, *, variant, on_progress):
        release.wait(timeout=5)

    app = create_app({})
    manager = DiarizationModelManager(command="helper", cache_root=tmp_path, prepare=blocking_prepare)
    app.extensions["diarization_models"] = manager
    client = app.test_client()

    assert client.post("/api/diarization/model/download").status_code == 202
    response = client.delete("/api/diarization/model")

    assert response.status_code == 409
    assert response.get_json()["error"]

    release.set()
    manager.wait_for_idle(timeout=5)
