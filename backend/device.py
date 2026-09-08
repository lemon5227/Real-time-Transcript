import platform
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class DeviceProfile:
    device: str
    kind: str
    memory_gb: Optional[float]
    performance: str


def _performance_for_memory(memory_gb: Optional[float]) -> str:
    if memory_gb is None:
        return "unknown"
    if memory_gb < 4:
        return "limited"
    if memory_gb < 8:
        return "balanced"
    return "fast"


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}


@lru_cache(maxsize=1)
def get_device_profile() -> DeviceProfile:
    try:
        import torch
    except ImportError:
        if _is_apple_silicon():
            return DeviceProfile("mps", "apple", None, "balanced")
        return DeviceProfile("cpu", "cpu", None, "unknown")

    if torch.cuda.is_available():
        try:
            properties = torch.cuda.get_device_properties(0)
            memory_gb = float(properties.total_memory) / (1024 ** 3)
            name = getattr(properties, "name", "")
            kind = "amd" if "amd" in name.lower() or "radeon" in name.lower() else "nvidia"
            return DeviceProfile("cuda", kind, memory_gb, _performance_for_memory(memory_gb))
        except Exception:
            return DeviceProfile("cuda", "nvidia", None, "unknown")

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return DeviceProfile("mps", "apple", None, "balanced")

    return DeviceProfile("cpu", "cpu", None, "unknown")


def recommend_local_model(profile: DeviceProfile) -> str:
    if profile.device == "cuda":
        if profile.memory_gb is not None and profile.memory_gb >= 12:
            return "large-v3-turbo"
        if profile.memory_gb is not None and profile.memory_gb >= 8:
            return "medium"
        if profile.memory_gb is not None and profile.memory_gb >= 4:
            return "small"
        return "base"
    if profile.device == "mps":
        return "parakeet-tdt-0.6b-v3"
    if profile.memory_gb is not None and profile.memory_gb <= 4:
        return "tiny"
    if profile.performance == "limited":
        return "base"
    return "small"


def runtime_for_profile(profile: DeviceProfile) -> str:
    """Return the execution runtime, separate from the model family."""
    if profile.device == "mps" and profile.kind == "apple":
        return "mlx"
    if profile.device == "cuda":
        return "cuda"
    return "cpu"


def device_public_dict(profile: Optional[DeviceProfile] = None):
    current = profile or get_device_profile()
    return {
        "device": current.device,
        "kind": current.kind,
        "memory_gb": current.memory_gb,
        "performance": current.performance,
        "runtime": runtime_for_profile(current),
        "recommended_model": recommend_local_model(current),
    }
