import sys
from pathlib import Path

import pytest

import quickstart
from quickstart import (
    BootstrapProfile,
    missing_cloud_settings,
    resolve_runtime_root,
    select_profile,
)

ROOT = Path(__file__).resolve().parents[1]


def test_auto_selects_mlx_on_apple_silicon():
    profile = select_profile("auto", "Darwin", "arm64", False)
    assert profile == BootstrapProfile("auto", "requirements-mac.txt", "Mac MLX")


def test_auto_selects_cuda_capable_local_runtime_when_nvidia_smi_works():
    profile = select_profile("auto", "Linux", "x86_64", True)
    assert profile == BootstrapProfile("auto", "requirements-local.txt", "CUDA/local")


def test_auto_selects_cloud_for_thin_cpu_machine():
    profile = select_profile("auto", "Windows", "AMD64", False)
    assert profile == BootstrapProfile("cloud", "requirements-cloud.txt", "cloud")


def test_explicit_local_overrides_hardware_detection():
    profile = select_profile("local", "Darwin", "arm64", False)
    assert profile == BootstrapProfile("local", "requirements-local.txt", "CPU/local")


def test_explicit_cloud_is_platform_independent():
    profile = select_profile("cloud", "Linux", "x86_64", True)
    assert profile == BootstrapProfile("cloud", "requirements-cloud.txt", "cloud")


def test_invalid_mode_is_rejected_before_setup():
    with pytest.raises(ValueError, match="auto, local or cloud"):
        select_profile("gpu", "Linux", "x86_64", True)


def test_missing_cloud_settings_only_returns_variable_names(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("CLOUD_BASE_URL=https://example.test/v1\nCLOUD_API_KEY=secret\n", encoding="utf-8")

    assert missing_cloud_settings(env_path) == ["CLOUD_TRANSCRIPTION_MODEL"]


def test_runtime_root_defaults_to_source_root(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("TRANSCRIPT_ENV_FILE", raising=False)

    assert resolve_runtime_root(tmp_path) == tmp_path


def test_runtime_root_prefers_explicit_runtime_directory(tmp_path: Path, monkeypatch):
    runtime_root = tmp_path / "Application Support" / "Real-time Transcript"
    env_file = tmp_path / "legacy" / ".env"
    monkeypatch.setenv("TRANSCRIPT_RUNTIME_DIR", str(runtime_root))
    monkeypatch.setenv("TRANSCRIPT_ENV_FILE", str(env_file))

    assert resolve_runtime_root(tmp_path) == runtime_root


def test_runtime_root_uses_env_file_parent_when_runtime_dir_is_unset(
    tmp_path: Path, monkeypatch
):
    env_file = tmp_path / "Application Support" / "Real-time Transcript" / ".env"
    monkeypatch.delenv("TRANSCRIPT_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TRANSCRIPT_ENV_FILE", str(env_file))

    assert resolve_runtime_root(tmp_path) == env_file.parent


def test_ensure_env_file_can_write_to_external_runtime_root(tmp_path: Path):
    source_root = tmp_path / "app"
    runtime_root = tmp_path / "Application Support" / "Real-time Transcript"
    source_root.mkdir()
    (source_root / ".env.example").write_text("PORT=54321\n", encoding="utf-8")

    assert quickstart.ensure_env_file(source_root, runtime_root=runtime_root) is True
    assert (runtime_root / ".env").read_text(encoding="utf-8") == "PORT=54321\n"
    assert not (source_root / ".env").exists()


def test_ensure_venv_can_live_under_external_runtime_root(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "app"
    runtime_root = tmp_path / "Application Support" / "Real-time Transcript"
    source_root.mkdir()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        venv_path = Path(command[-1])
        python_path = venv_path / "bin" / "python"
        python_path.parent.mkdir(parents=True, exist_ok=True)
        python_path.touch()

    monkeypatch.setattr(quickstart.subprocess, "run", fake_run)

    python_path = quickstart.ensure_venv(
        source_root,
        bootstrap_python=Path(sys.executable),
        runtime_root=runtime_root,
    )

    assert python_path == runtime_root / ".venv" / "bin" / "python"
    assert calls[0][0][-3:] == ["-m", "venv", str(runtime_root / ".venv")]


def test_windows_launcher_translates_power_shell_mode_switch():
    content = (ROOT / "quickstart.ps1").read_text(encoding="utf-8")

    assert "param(" in content
    assert "ValidateSet(\"auto\", \"local\", \"cloud\")" in content
    assert "--mode $Mode" in content


def test_mlx_profile_requires_python_310():
    with pytest.raises(RuntimeError, match=r"Python 3.10\+"):
        quickstart.validate_python_version((3, 9), "requirements-mac.txt")


def test_bootstrap_prefers_compatible_versioned_python(monkeypatch):
    candidates = [Path("/usr/bin/python3"), Path("/opt/homebrew/bin/python3.12")]
    versions = {candidates[0]: (3, 9), candidates[1]: (3, 12)}
    monkeypatch.setattr(quickstart, "python_candidates", lambda: candidates)
    monkeypatch.setattr(quickstart, "read_python_version", lambda path: versions[path])

    assert quickstart.find_bootstrap_python("requirements-mac.txt") == candidates[1]


def test_cli_turns_setup_error_into_actionable_exit(monkeypatch, capsys):
    def fail(_argv=None):
        raise RuntimeError("requirements-mac.txt requires Python 3.10+")

    monkeypatch.setattr(quickstart, "main", fail)

    assert quickstart.run_cli([]) == 2
    assert "Setup could not continue" in capsys.readouterr().err
