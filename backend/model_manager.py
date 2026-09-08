"""Local model cache inspection and asynchronous downloads for the web UI."""

from __future__ import annotations

import hashlib
import importlib
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, Mapping, Optional
from urllib.parse import urlparse


class ModelManager:
    """Report Whisper weight readiness and download one model at a time.

    The manager deliberately does not import Whisper during construction. This keeps
    the page and capabilities endpoints fast on machines without local dependencies.
    """

    def __init__(
        self,
        catalog: Iterable[Mapping[str, object]],
        dependency_checker: Optional[Callable[[str], bool]] = None,
        cache_root: Optional[Path] = None,
    ):
        self._catalog = {str(model["id"]): dict(model) for model in catalog}
        self._dependency_checker = dependency_checker or self._default_dependency_checker
        self._cache_root = Path(cache_root) if cache_root else self._default_cache_root()
        self._states: Dict[str, Dict[str, object]] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _default_dependency_checker(model_id: str) -> bool:
        try:
            module = importlib.import_module("backend.providers.local_whisper")
            return bool(module.local_model_available(model_id))
        except (ImportError, AttributeError):
            return False

    @staticmethod
    def _default_cache_root() -> Path:
        configured = os.environ.get("WHISPER_CACHE_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
        if xdg_cache:
            return Path(xdg_cache).expanduser() / "whisper"
        return Path.home() / ".cache" / "whisper"

    def list_models(self):
        with self._lock:
            return [self._snapshot(model_id) for model_id in self._catalog]

    def get_model(self, model_id: str):
        with self._lock:
            if model_id not in self._catalog:
                return None
            return self._snapshot(model_id)

    def start_download(self, model_id: str):
        with self._lock:
            if model_id not in self._catalog:
                raise KeyError(model_id)
            current = self._snapshot(model_id)
            if current["status"] == "downloading":
                return current
            if not current["dependency_available"]:
                return current
            if current["weights_available"]:
                return current
            url = self._resolve_url(model_id)
            if not url:
                self._states[model_id] = {
                    "status": "runtime_download",
                    "message": "本地运行时会在首次启动时下载模型",
                    "error": None,
                }
                return self._snapshot(model_id)
            cancel_event = threading.Event()
            self._cancel_events[model_id] = cancel_event
            self._states[model_id] = {
                "status": "downloading",
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "progress": 0,
                "message": "正在准备下载",
                "error": None,
            }
            worker = threading.Thread(
                target=self._download_model,
                args=(model_id, url, cancel_event),
                daemon=True,
                name="model-download-%s" % model_id,
            )
            worker.start()
            return self._snapshot(model_id)

    def cancel_download(self, model_id: str):
        with self._lock:
            if model_id not in self._catalog:
                raise KeyError(model_id)
            event = self._cancel_events.get(model_id)
            if event is not None:
                event.set()
            return self._snapshot(model_id)

    def wait_for(self, model_id: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.get_model(model_id)
            if snapshot is None or snapshot["status"] != "downloading":
                return snapshot
            time.sleep(0.01)
        return self.get_model(model_id)

    def _resolve_url(self, model_id: str) -> Optional[str]:
        configured = self._catalog[model_id].get("url")
        if configured:
            return str(configured)
        try:
            whisper = importlib.import_module("whisper")
            return whisper._MODELS.get(model_id)  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            return None

    def _target_path(self, model_id: str, url: Optional[str] = None) -> Path:
        filename = Path(urlparse(url).path).name if url else "%s.pt" % model_id
        return self._cache_root / (filename or "%s.pt" % model_id)

    def _snapshot(self, model_id: str):
        model = dict(self._catalog[model_id])
        dependency_available = bool(self._dependency_checker(model_id))
        url = self._resolve_url(model_id) if dependency_available else None
        target = self._target_path(model_id, url)
        downloaded_bytes = target.stat().st_size if target.is_file() else 0
        weights_available = target.is_file() and downloaded_bytes > 0
        state = dict(self._states.get(model_id, {}))
        status = str(state.get("status") or ("dependency_missing" if not dependency_available else "ready" if weights_available else "not_downloaded" if url else "runtime_download"))
        if status == "downloading":
            downloaded_bytes = int(state.get("downloaded_bytes") or downloaded_bytes)
        total_bytes = int(state.get("total_bytes") or 0)
        progress = int(state.get("progress") or (100 if weights_available else 0))
        model.update(
            {
                "available": dependency_available,
                "dependency_available": dependency_available,
                "download_supported": bool(url),
                "weights_available": weights_available,
                "status": status,
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "progress": min(100, max(0, progress)),
                "message": state.get("message") or self._default_message(status),
            }
        )
        if state.get("error"):
            model["error"] = state["error"]
        return model

    @staticmethod
    def _default_message(status: str) -> str:
        return {
            "ready": "模型已下载，可以开始听课",
            "not_downloaded": "首次使用需要下载模型",
            "dependency_missing": "本地转录依赖未安装",
            "downloading": "正在下载模型",
            "failed": "模型下载失败，请重试",
            "runtime_download": "本地运行时会在首次启动时下载模型",
        }.get(status, "等待模型状态")

    def _set_state(self, model_id: str, **changes: object) -> None:
        with self._lock:
            state = dict(self._states.get(model_id, {}))
            state.update(changes)
            self._states[model_id] = state

    def _download_model(self, model_id: str, url: str, cancel_event: threading.Event) -> None:
        target = self._target_path(model_id, url)
        partial = target.with_name(target.name + ".part")
        try:
            self._cache_root.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=30) as response:
                headers = response.info()
                total = int(headers.get("Content-Length") or 0)
                self._set_state(
                    model_id,
                    total_bytes=total,
                    message="正在下载模型",
                )
                downloaded = 0
                with partial.open("wb") as output:
                    while True:
                        if cancel_event.is_set():
                            raise _DownloadCancelled()
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        progress = round(downloaded * 100 / total) if total else 0
                        self._set_state(
                            model_id,
                            downloaded_bytes=downloaded,
                            total_bytes=total,
                            progress=progress,
                        )
            if cancel_event.is_set():
                raise _DownloadCancelled()
            expected_sha = self._expected_sha256(url)
            if expected_sha and self._sha256(partial) != expected_sha:
                raise RuntimeError("下载完成但校验失败，请重试")
            os.replace(partial, target)
            self._set_state(
                model_id,
                status="ready",
                downloaded_bytes=target.stat().st_size,
                total_bytes=target.stat().st_size,
                progress=100,
                message="模型已下载，可以开始听课",
                error=None,
            )
        except _DownloadCancelled:
            self._safe_unlink(partial)
            self._set_state(
                model_id,
                status="not_downloaded",
                downloaded_bytes=0,
                total_bytes=0,
                progress=0,
                message="已取消下载",
                error=None,
            )
        except Exception as exc:
            self._safe_unlink(partial)
            self._set_state(
                model_id,
                status="failed",
                message="模型下载失败，请检查网络后重试",
                error=str(exc),
            )
        finally:
            with self._lock:
                self._cancel_events.pop(model_id, None)

    @staticmethod
    def _expected_sha256(url: str) -> Optional[str]:
        parts = url.rstrip("/").split("/")
        candidate = parts[-2] if len(parts) >= 2 else ""
        return candidate if len(candidate) == 64 else None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class _DownloadCancelled(Exception):
    pass
