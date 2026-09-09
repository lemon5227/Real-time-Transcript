#!/usr/bin/env python3
"""Prepare the appropriate runtime and launch Real-time Transcript.

This module intentionally uses only Python's standard library so it can run
before the project's virtual environment has been created.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

VALID_MODES = {"auto", "local", "cloud"}
CLOUD_SETTINGS = ("CLOUD_BASE_URL", "CLOUD_API_KEY", "CLOUD_TRANSCRIPTION_MODEL")


@dataclass(frozen=True)
class BootstrapProfile:
    mode: str
    requirements_file: str
    runtime_label: str


def select_profile(
    mode: str,
    system: str,
    machine: str,
    nvidia_available: bool,
) -> BootstrapProfile:
    """Choose the dependency set and runtime mode for a machine."""

    normalized_mode = mode.strip().lower()
    if normalized_mode not in VALID_MODES:
        raise ValueError("mode must be auto, local or cloud")

    if normalized_mode == "cloud":
        return BootstrapProfile("cloud", "requirements-cloud.txt", "cloud")
    if normalized_mode == "local":
        return BootstrapProfile("local", "requirements-local.txt", "CPU/local")
    if system == "Darwin" and machine.lower() in {"arm64", "aarch64"}:
        return BootstrapProfile("auto", "requirements-mac.txt", "Mac MLX")
    if nvidia_available:
        return BootstrapProfile("auto", "requirements-local.txt", "CUDA/local")
    return BootstrapProfile("cloud", "requirements-cloud.txt", "cloud")


def ensure_env_file(root: Path) -> bool:
    """Create `.env` from the example only when the user has none."""

    env_path = root / ".env"
    if env_path.exists():
        return False
    example_path = root / ".env.example"
    if not example_path.exists():
        raise FileNotFoundError("Missing .env.example")
    shutil.copyfile(example_path, env_path)
    return True


def _venv_python(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def ensure_venv(root: Path) -> Path:
    """Create or reuse the project virtual environment."""

    venv_path = root / ".venv"
    python_path = _venv_python(venv_path)
    if not python_path.exists():
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            cwd=root,
            check=True,
        )
    if not python_path.exists():
        raise RuntimeError("The virtual environment was created without a Python interpreter")
    return python_path


def install_requirements(python: Path, requirements_file: Path) -> None:
    """Install one runtime profile without exposing environment values."""

    if not requirements_file.exists():
        raise FileNotFoundError(f"Missing requirements file: {requirements_file.name}")
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(requirements_file)],
        check=True,
    )


def _env_file_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def missing_cloud_settings(env_path: Path) -> list[str]:
    """Return missing cloud variable names without returning their values."""

    values = _env_file_values(env_path)
    return [name for name in CLOUD_SETTINGS if not values.get(name)]


def _nvidia_available() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and launch Real-time Transcript for this device."
    )
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        default="auto",
        help="Runtime selection: auto, local, or cloud (default: auto)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(__file__).resolve().parent
    profile = select_profile(args.mode, platform.system(), platform.machine(), _nvidia_available())

    created_env = ensure_env_file(root)
    if created_env:
        print("Created .env from .env.example; add cloud settings only if cloud mode is needed.")

    python_path = ensure_venv(root)
    print(f"Installing runtime dependencies for {profile.runtime_label}...")
    install_requirements(python_path, root / profile.requirements_file)

    environment = os.environ.copy()
    if profile.mode != "auto":
        environment["TRANSCRIPTION_MODE"] = profile.mode

    if profile.mode == "cloud":
        missing = missing_cloud_settings(root / ".env")
        if missing:
            print("Cloud transcription is not configured yet. Missing variable names: " + ", ".join(missing))

    print(f"Starting Real-time Transcript ({profile.runtime_label})...")
    completed = subprocess.run(
        [str(python_path), str(root / "app.py")],
        cwd=root,
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
