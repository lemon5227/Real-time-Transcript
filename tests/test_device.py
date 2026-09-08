from backend.device import DeviceProfile, recommend_local_model


def test_cpu_low_memory_recommends_tiny_or_base():
    profile = DeviceProfile(device="cpu", kind="cpu", memory_gb=4.0, performance="limited")
    assert recommend_local_model(profile) in {"tiny", "base"}


def test_fast_cuda_recommends_large_family():
    profile = DeviceProfile(device="cuda", kind="nvidia", memory_gb=12.0, performance="fast")
    assert recommend_local_model(profile) in {"medium", "large-v3-turbo"}


def test_mps_recommends_lightweight_english_distil_model():
    profile = DeviceProfile(device="mps", kind="apple", memory_gb=None, performance="balanced")
    assert recommend_local_model(profile) == "distil-small.en"
