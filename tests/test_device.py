import sys

import backend.device as device_module
from backend.device import (
    DeviceProfile,
    device_public_dict,
    recommend_local_model,
    runtime_for_profile,
)


def test_cpu_low_memory_recommends_tiny_or_base():
    profile = DeviceProfile(device="cpu", kind="cpu", memory_gb=4.0, performance="limited")
    assert recommend_local_model(profile) in {"tiny", "base"}


def test_fast_cuda_recommends_large_family():
    profile = DeviceProfile(device="cuda", kind="nvidia", memory_gb=12.0, performance="fast")
    assert recommend_local_model(profile) in {"medium", "large-v3-turbo"}


def test_mps_recommends_apple_silicon_mlx_model():
    profile = DeviceProfile(device="mps", kind="apple", memory_gb=None, performance="balanced")
    assert recommend_local_model(profile) == "parakeet-tdt-0.6b-v3"


def test_mps_device_reports_mlx_runtime():
    profile = DeviceProfile(device="mps", kind="apple", memory_gb=None, performance="balanced")
    assert device_public_dict(profile)["runtime"] == "mlx"


def test_cuda_device_reports_cuda_runtime():
    profile = DeviceProfile(device="cuda", kind="nvidia", memory_gb=8.0, performance="fast")
    assert runtime_for_profile(profile) == "cuda"
    assert device_public_dict(profile)["runtime"] == "cuda"


def test_cpu_device_reports_cpu_runtime():
    profile = DeviceProfile(device="cpu", kind="cpu", memory_gb=4.0, performance="limited")
    assert runtime_for_profile(profile) == "cpu"
    assert device_public_dict(profile)["runtime"] == "cpu"


def test_arm64_mac_reports_mlx_without_torch(monkeypatch):
    monkeypatch.setattr(device_module.sys, "platform", "darwin")
    monkeypatch.setattr(device_module.platform, "machine", lambda: "arm64")
    monkeypatch.setitem(sys.modules, "torch", None)
    device_module.get_device_profile.cache_clear()
    try:
        profile = device_module.get_device_profile()
    finally:
        device_module.get_device_profile.cache_clear()

    assert profile == DeviceProfile("mps", "apple", None, "balanced")
