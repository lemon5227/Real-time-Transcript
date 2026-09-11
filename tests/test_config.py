import pytest

from backend.config import load_config


def test_config_defaults_to_localhost_and_auto_mode():
    config = load_config({})
    assert config.host == "127.0.0.1"
    assert config.port == 5001
    assert config.debug is False
    assert config.transcription_mode == "auto"


def test_config_loads_dotenv_from_explicit_external_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / "Application Support" / "Real-time Transcript" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("HOST=127.0.0.1\nPORT=54321\n", encoding="utf-8")
    monkeypatch.setenv("TRANSCRIPT_ENV_FILE", str(env_file))
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    config = load_config()

    assert config.port == 54321
    assert "http://127.0.0.1:54321" in config.cors_origins


def test_local_port_is_allowed_when_port_is_overridden():
    config = load_config({"PORT": "5002"})

    assert "http://127.0.0.1:5002" in config.cors_origins
    assert "http://localhost:5002" in config.cors_origins


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


def test_streaming_chunk_and_right_context_are_configurable():
    """Both knobs trade latency against accuracy/CPU, so neither is hardcoded."""
    config = load_config({})
    assert config.streaming_chunk_seconds == 1.0
    assert config.mlx_stream_right_context == 32

    configured = load_config(
        {"STREAMING_CHUNK_SECONDS": "0.5", "MLX_STREAM_RIGHT_CONTEXT": "8"}
    )
    assert configured.streaming_chunk_seconds == 0.5
    assert configured.mlx_stream_right_context == 8


def test_public_config_reports_the_streaming_confirmation_lag():
    """One encoder frame is 8 * 160 / 16000 = 0.08s of held-back audio."""
    config = load_config({})
    audio = config.public_dict()["audio"]
    assert audio["streaming_chunk_seconds"] == 1.0
    assert audio["streaming_lag_seconds"] == pytest.approx(32 * 0.08)
    assert config.streaming_confirmation_lag_seconds == pytest.approx(2.56)


def test_public_config_never_contains_api_key():
    config = load_config({
        "CLOUD_BASE_URL": "https://example.test/v1",
        "CLOUD_API_KEY": "secret-value",
        "CLOUD_TRANSCRIPTION_MODEL": "transcribe-test",
    })
    public = config.public_dict()
    assert "secret-value" not in repr(public)
    assert public["cloud"]["configured"] is True
