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


def test_render_blueprint_is_cloud_only_and_has_health_check():
    content = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "runtime: docker" in content
    assert "dockerfilePath: ./Dockerfile" in content
    assert "healthCheckPath: /api/health" in content
    assert "TRANSCRIPTION_MODE" in content
    assert "CLOUD_BASE_URL" in content
    assert "CLOUD_API_KEY" in content
    assert "CLOUD_TRANSCRIPTION_MODEL" in content


def test_readme_contains_one_click_deploy_link_and_local_override():
    content = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "render.com/deploy?repo=https://github.com/lemon5227/Real-time-Transcript" in content
    assert "TRANSCRIPT_DOCKERFILE=Dockerfile.local" in content
    assert "DOCKER_TRANSCRIPTION_MODE=local" in content

def test_pytest_resolves_the_backend_package_from_the_repository_root():
    content = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "pythonpath = ." in content


def test_ci_runs_lint_tests_and_browser_javascript():
    content = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "requirements-dev.txt" in content
    assert "ruff check ." in content
    assert "pytest -q" in content
    assert "node --check" in content
