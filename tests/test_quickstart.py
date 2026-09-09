from pathlib import Path

import pytest

from quickstart import BootstrapProfile, missing_cloud_settings, select_profile


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
