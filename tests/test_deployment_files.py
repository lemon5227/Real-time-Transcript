from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_dockerfile_uses_cloud_requirements_and_gunicorn():
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-cloud.txt" in content
    assert "gunicorn" in content
    assert "workers 1" in content


def test_local_dockerfile_uses_local_requirements():
    content = (ROOT / "Dockerfile.local").read_text(encoding="utf-8")
    assert "requirements-local.txt" in content


def test_dockerignore_excludes_env_and_model_data():
    content = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in content
    assert "models/" in content
    assert ".venv/" in content


def test_compose_selects_dockerfile_and_host_port_from_environment():
    content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "TRANSCRIPT_DOCKERFILE" in content
    assert "TRANSCRIPT_PORT" in content
    assert "HOST: 0.0.0.0" in content
    assert "TRANSCRIPTION_MODE: ${DOCKER_TRANSCRIPTION_MODE:-cloud}" in content
