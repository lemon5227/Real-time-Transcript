from backend.device import DeviceProfile, device_public_dict, recommend_local_model


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
