from backend import create_app


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


def test_model_download_endpoint_rejects_unknown_model():
    app = create_app({})
    response = app.test_client().post("/api/models/not-a-model/download")
    assert response.status_code == 404
