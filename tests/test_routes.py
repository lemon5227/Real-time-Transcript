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


def test_health_endpoint_is_available():
    app = create_app({})
    assert app.test_client().get("/api/health").get_json()["status"] == "ok"
