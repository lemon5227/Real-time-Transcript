from backend.config import load_config


def test_config_defaults_to_localhost_and_auto_mode():
    config = load_config({})
    assert config.host == "127.0.0.1"
    assert config.port == 5001
    assert config.debug is False
    assert config.transcription_mode == "auto"


def test_default_audio_queue_covers_model_startup_buffer():
    config = load_config({})
    assert config.audio_max_queue == 64


def test_startup_timeout_is_configurable_and_reaches_the_browser():
    """The browser buffers audio for exactly as long as the backend waits."""
    config = load_config({})
    assert config.audio_startup_timeout_seconds == 45.0
    assert config.public_dict()["audio"]["startup_timeout_seconds"] == 45.0

    configured = load_config({"AUDIO_STARTUP_TIMEOUT_SECONDS": "20"})
    assert configured.audio_startup_timeout_seconds == 20.0


def test_public_config_never_contains_api_key():
    config = load_config({
        "CLOUD_BASE_URL": "https://example.test/v1",
        "CLOUD_API_KEY": "secret-value",
        "CLOUD_TRANSCRIPTION_MODEL": "transcribe-test",
    })
    public = config.public_dict()
    assert "secret-value" not in repr(public)
    assert public["cloud"]["configured"] is True
