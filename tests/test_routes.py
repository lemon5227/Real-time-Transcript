import io

import pytest

from backend import create_app
from backend.device import DeviceProfile


class ImmediateFineManager:
    def __init__(self):
        self.jobs = {}

    def start(self, audio_bytes, suffix, language, model_ref):
        self.jobs["job-1"] = {
            "job_id": "job-1",
            "status": "ready",
            "segments": [],
            "language": language,
            "model": model_ref,
        }
        return {"job_id": "job-1", "status": "ready"}

    def get(self, job_id):
        return self.jobs.get(job_id)


def test_capabilities_redact_cloud_api_key():
    app = create_app({
        "CLOUD_BASE_URL": "https://example.test/v1",
        "CLOUD_API_KEY": "secret-value",
        "CLOUD_TRANSCRIPTION_MODEL": "transcribe-test",
    })
    response = app.test_client().get("/api/capabilities")
    assert response.status_code == 200
    body = response.get_json()
    assert body["cloud"]["configured"] is True
    assert "secret-value" not in response.get_data(as_text=True)
    assert "ready_models" in body["local"]


def test_capabilities_report_translation_providers_without_keys():
    app = create_app({
        "TRANSLATION_GOOGLE_PROJECT_ID": "project-1",
        "TRANSLATION_GOOGLE_API_KEY": "google-secret",
        "TRANSLATION_MICROSOFT_API_KEY": "microsoft-secret",
    })
    response = app.test_client().get("/api/capabilities")
    body = response.get_json()
    assert body["translation"]["google"]["configured"] is True
    assert body["translation"]["microsoft"]["configured"] is True
    assert "secret" not in response.get_data(as_text=True)


def test_capabilities_expose_the_streaming_latency_knobs():
    """The UI needs these to explain and tune caption lag."""
    app = create_app({"MLX_STREAM_RIGHT_CONTEXT": "16", "STREAMING_CHUNK_SECONDS": "0.5"})
    audio = app.test_client().get("/api/capabilities").get_json()["audio"]

    assert audio["streaming_chunk_seconds"] == 0.5
    assert audio["streaming_lag_seconds"] == pytest.approx(16 * 0.08)


def test_capabilities_expose_the_active_live_decoder():
    """`audio` is built by hand here rather than from `public_dict()`, so this
    guards the two copies against drifting apart."""
    app = create_app({"MLX_WINDOW_SECONDS": "12", "MLX_HOP_SECONDS": "3"})
    audio = app.test_client().get("/api/capabilities").get_json()["audio"]

    assert audio["live"] == {
        "mode": "windowed",
        "window_seconds": 12.0,
        "hop_seconds": 3.0,
        "confirmation_lag_seconds": 0.0,
    }


def test_health_endpoint_is_available():
    app = create_app({})
    assert app.test_client().get("/api/health").get_json()["status"] == "ok"


def test_models_endpoint_reports_readiness_fields():
    app = create_app({})
    response = app.test_client().get("/api/models")
    assert response.status_code == 200
    model = response.get_json()["models"][0]
    assert {"status", "dependency_available", "download_supported", "weights_available", "progress"}.issubset(model)


def test_models_endpoint_exposes_selection_guidance():
    app = create_app({})
    models = app.test_client().get("/api/models").get_json()["models"]
    model = models[0]
    assert {"speed", "quality", "resource", "languages", "best_for"}.issubset(model)


def test_catalog_marks_runtime_families():
    app = create_app({})
    models = app.test_client().get("/api/models").get_json()["models"]
    by_id = {model["id"]: model for model in models}
    assert by_id["parakeet-tdt-0.6b-v3"]["runtime"] == "mlx"
    assert by_id["small"]["runtime"] == "standard"
    assert by_id["parakeet-tdt-0.6b-v3"]["model_ref"] == "mlx-community/parakeet-tdt-0.6b-v3"


def test_capabilities_only_count_models_for_active_runtime(monkeypatch):
    from backend import routes

    monkeypatch.setattr(
        routes,
        "get_device_profile",
        lambda: DeviceProfile("mps", "apple", None, "balanced"),
    )
    app = create_app({})

    class FakeModelManager:
        def list_models(self):
            return [
                {"id": "parakeet-tdt-0.6b-v3", "runtime": "mlx", "dependency_available": False, "status": "dependency_missing"},
                {"id": "small", "runtime": "standard", "dependency_available": True, "status": "ready"},
            ]

    app.extensions["model_manager"] = FakeModelManager()
    body = app.test_client().get("/api/capabilities").get_json()

    assert body["local"]["available"] is False
    assert body["local"]["ready_models"] == []


def test_model_download_endpoint_rejects_unknown_model():
    app = create_app({})
    response = app.test_client().post("/api/models/not-a-model/download")
    assert response.status_code == 404


def test_refine_transcription_requires_audio():
    app = create_app({})

    response = app.test_client().post("/api/refine-transcription")

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "FINE_TRANSCRIPTION_INVALID_AUDIO"


def test_refine_transcription_returns_pollable_job():
    app = create_app({})
    app.extensions["fine_transcription_manager"] = ImmediateFineManager()
    client = app.test_client()

    response = client.post(
        "/api/refine-transcription",
        data={"audio": (io.BytesIO(b"wav"), "lecture.webm"), "language": "en"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    job_id = response.get_json()["job_id"]
    assert client.get(f"/api/refine-transcription/{job_id}").get_json()["status"] == "ready"
