"""Disk state and one-at-a-time downloads for the Nemotron speaker model.

The settings page must answer "已下载了吗 / 正在下载吗 / 占用多少空间" without
starting a lecture, so this manager inspects the shared FluidAudio cache the
Swift helper reads and drives `prepare_nemotron_models` on a background thread.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from .logging_setup import get_logger
from .providers.diarization import prepare_nemotron_models

logger = get_logger("diarization-models")

MODEL_FOLDER_NAME = "nemotron-3-diarization"
SILENCE_EMBEDDING_FILE = "learnable_sil_emb.bin"
BUNDLE_MANIFEST_FILE = "coremldata.bin"

# Mirrors the Swift helper's default cache location so the card and the live
# session always look at the same directory.
DEFAULT_CACHE_ROOT = Path.home() / "Library" / "Application Support" / "FluidAudio" / "Models"


class DiarizationModelError(RuntimeError):
    """Raised when a request cannot start (component missing, busy)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def default_model_cache_root(environ=None) -> Path:
    """Resolve the FluidAudio cache root the way the Swift helper does."""
    source = os.environ if environ is None else environ
    configured = str(source.get("NEMOTRON_MODEL_DIR") or "").strip()
    root = Path(configured) if configured else DEFAULT_CACHE_ROOT
    # The launcher may point at the model folder itself; step one level up so
    # the rest of the code can always join the folder name.
    if root.name == MODEL_FOLDER_NAME:
        root = root.parent
    return root


class DiarizationModelManager:
    """Track presence, size, and the single prepared download of the model.

    The constructor takes the helper command and a `prepare` callable so tests
    can run the full state machine without spawning the Swift binary.
    """

    def __init__(
        self,
        *,
        command: str = "",
        variant: str = "low",
        cache_root: Optional[Path] = None,
        prepare: Optional[Callable[..., None]] = None,
    ) -> None:
        self._command = command or ""
        self._variant = variant or "low"
        self._cache_root = Path(cache_root) if cache_root else default_model_cache_root()
        self._prepare = prepare or prepare_nemotron_models
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._state: Dict[str, object] = {}

    @property
    def directory(self) -> Path:
        return self._cache_root / MODEL_FOLDER_NAME

    def snapshot(self) -> Dict[str, object]:
        present, total_bytes = self._inspect()
        with self._lock:
            state = dict(self._state)
            worker = self._worker
        downloading = bool(worker and worker.is_alive())
        if downloading:
            status = "downloading"
        elif present:
            status = "ready"
        elif state.get("status") == "failed":
            status = "failed"
        else:
            status = "not_downloaded"
        if status == "ready":
            progress = 100
        else:
            progress = int(state.get("progress") or 0)
        return {
            "id": MODEL_FOLDER_NAME,
            "variant": self._variant,
            "status": status,
            "progress": progress,
            "message": state.get("message") if status in ("downloading", "failed") else None,
            "error": state.get("error") if status == "failed" else None,
            "present": present,
            "downloaded_bytes": total_bytes,
            "component_available": bool(self._command),
            "directory": str(self.directory),
        }

    def start_download(self) -> Dict[str, object]:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return self.snapshot()
            present, _ = self._inspect()
            if present:
                return self.snapshot()
            if not self._command:
                raise DiarizationModelError("component_missing", "本机没有安装说话人识别组件")
            self._state = {
                "status": "downloading",
                "progress": 0,
                "message": "正在从 Hugging Face 下载说话人模型",
                "error": None,
            }
            worker = threading.Thread(
                target=self._download,
                name="nemotron-model-download",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return self.snapshot()

    def delete(self) -> Dict[str, object]:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise DiarizationModelError("busy", "模型正在下载，请稍后再删除")
            directory = self.directory
            if directory.exists():
                try:
                    shutil.rmtree(directory)
                except OSError as exc:
                    raise DiarizationModelError(
                        "delete_failed", f"无法删除模型文件: {exc}"
                    ) from exc
            self._state = {}
            return self.snapshot()

    def wait_for_idle(self, timeout: Optional[float] = None) -> None:
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _download(self) -> None:
        def on_progress(fraction: float) -> None:
            with self._lock:
                if self._state.get("status") == "downloading":
                    self._state["progress"] = max(0, min(100, int(round(fraction * 100))))

        try:
            self._prepare(self._command, variant=self._variant, on_progress=on_progress)
        except Exception as exc:  # Any failure must surface on the card, not kill the thread.
            logger.warning("nemotron diarizer download failed: %s", exc)
            with self._lock:
                self._state = {
                    "status": "failed",
                    "progress": int(self._state.get("progress") or 0),
                    "message": str(exc) or "说话人模型下载失败",
                    "error": str(exc) or "说话人模型下载失败",
                }
            return
        with self._lock:
            self._state = {"status": "ready", "progress": 100, "message": None, "error": None}

    def _inspect(self):
        directory = self.directory
        if not directory.is_dir():
            return False, 0
        bundles = [path for path in directory.glob("*/Nemotron3Diarizer_*.mlmodelc") if path.is_dir()]
        present = (directory / SILENCE_EMBEDDING_FILE).is_file() and any(
            (bundle / BUNDLE_MANIFEST_FILE).is_file() for bundle in bundles
        )
        total_bytes = 0
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    continue
        return present, total_bytes
