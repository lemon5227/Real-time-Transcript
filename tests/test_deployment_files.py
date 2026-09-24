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


def test_every_module_the_suite_patches_is_installed_for_development():
    """A test that patches `requests.post` needs `requests` in the *dev* environment.

    `requirements-core.txt` deliberately omits `requests` — only the cloud and translation
    providers need it, and they turn a missing import into `CLOUD_DEPENDENCY_MISSING`. The
    provider tests do not import it either; they reach it through
    `monkeypatch.setattr("requests.post", ...)`, which imports the module by name at call
    time.

    That combination made the suite pass on the author's machine — whose venv also has
    `requirements-mac.txt` installed — while CI, which installs only `requirements-dev.txt`,
    failed all twelve patching tests with `ModuleNotFoundError: No module named 'requests'`.
    Nothing in the suite noticed, because the failure only appears in an environment nobody
    ran. This test demands every patched top-level module be importable here too, so adding
    a patch for a package that dev requirements do not install fails locally instead of in CI.
    """
    import importlib
    import re

    patched = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for dotted in re.findall(r'monkeypatch\.setattr\(\s*"([\w.]+)"', path.read_text(encoding="utf-8")):
            if "." in dotted:
                patched.add(dotted.split(".")[0])

    # `backend` is this project; the standard library ships with the interpreter and
    # always imports, so filtering stdlib names out would only rename the survivors —
    # and sys.stdlib_module_names does not exist on Python 3.9, which README claims.
    third_party = sorted(patched - {"backend"})
    assert "requests" in third_party, "the patch scanner stopped finding requests; fix the pattern"

    missing = []
    for name in third_party:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)

    assert not missing, (
        "requirements-dev.txt does not install modules the test suite patches: %s. "
        "CI installs only requirements-dev.txt, so the suite must pass from a venv built "
        "from that file alone." % ", ".join(missing)
    )


def test_macos_dmg_workflow_builds_and_publishes_artifact():
    workflow = (ROOT / ".github" / "workflows" / "macos-dmg.yml").read_text(encoding="utf-8")
    for token in [
        "workflow_dispatch",
        'tags: ["v*"]',
        "runs-on: macos-14",
        "brew install librsvg",
        "bash scripts/build-macos-dmg.sh",
        "hdiutil imageinfo",
        "actions/upload-artifact@v6",
        "shiju-macos-dmg",
        "拾句-macOS.dmg",
        "contents: write",
        "gh release create",
    ]:
        assert token in workflow
    assert "OUTPUT_DIR: ${{ github.workspace }}/dist" in workflow
    assert "OUTPUT_DIR: ${{ runner.temp }}" not in workflow
