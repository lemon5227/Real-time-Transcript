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
    """Report local weight readiness and download one model at a time.

    The manager deliberately does not import Whisper during construction. This keeps
    the page and capabilities endpoints fast on machines without local dependencies.
    """

    def __init__(
        self,
        catalog: Iterable[Mapping[str, object]],
        dependency_checker: Optional[Callable[[str], bool]] = None,
        cache_root: Optional[Path] = None,
        runtime_cache_root: Optional[Path] = None,
    ):
        self._catalog = {str(model["id"]): dict(model) for model in catalog}
        self._dependency_checker = dependency_checker or self._default_dependency_checker
        self._cache_root = Path(cache_root) if cache_root else self._default_cache_root()
        self._runtime_cache_root = Path(runtime_cache_root) if runtime_cache_root else self._default_runtime_cache_root()
        self._states: Dict[str, Dict[str, object]] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def _default_dependency_checker(self, model_id: str) -> bool:
        runtime = str(self._catalog.get(model_id, {}).get("runtime") or "standard")
        if runtime == "mlx":
            try:
                return importlib.util.find_spec("parakeet_mlx") is not None
            except (ImportError, ValueError):
                return False
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

    @staticmethod
    def _default_runtime_cache_root() -> Path:
        configured = os.environ.get("HF_HUB_CACHE", "").strip()
        if configured:
            return Path(configured).expanduser()
        hf_home = os.environ.get("HF_HOME", "").strip()
        if hf_home:
            return Path(hf_home).expanduser() / "hub"
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
        if xdg_cache:
            return Path(xdg_cache).expanduser() / "huggingface" / "hub"
        return Path.home() / ".cache" / "huggingface" / "hub"

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
            if current["weights_available"]:
                return current
            if self._is_runtime_model(model_id):
                if not current["download_supported"]:
                    return current
                cancel_event = threading.Event()
                self._cancel_events[model_id] = cancel_event
                self._states[model_id] = {
                    "status": "downloading",
                    "downloaded_bytes": 0,
                    "total_bytes": 0,
                    "progress": 0,
                    "message": "正在从 Hugging Face 下载模型",
                    "error": None,
                }
                worker = threading.Thread(
                    target=self._download_runtime_model,
                    args=(model_id, cancel_event),
                    daemon=True,
                    name="model-download-%s" % model_id,
                )
                worker.start()
                return self._snapshot(model_id)
            url = self._resolve_url(model_id)
            if not url:
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
        if self._is_runtime_model(model_id):
            return None
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

    def _is_runtime_model(self, model_id: str) -> bool:
        return str(self._catalog[model_id].get("runtime") or "standard") == "mlx"

    def _runtime_cache_files(self, model_id: str) -> Dict[str, Optional[str]]:
        if not self._is_runtime_model(model_id):
            return {}
        model_ref = str(self._catalog[model_id].get("model_ref") or "").strip()
        if not model_ref:
            return {"config.json": None, "model.safetensors": None}
        try:
            huggingface = importlib.import_module("huggingface_hub")
            return {
                filename: huggingface.try_to_load_from_cache(
                    model_ref,
                    filename,
                    cache_dir=str(self._runtime_cache_root),
                )
                for filename in ("config.json", "model.safetensors")
            }
        except (ImportError, AttributeError, OSError, TypeError, ValueError):
            return {"config.json": None, "model.safetensors": None}

    def _snapshot(self, model_id: str):
        model = dict(self._catalog[model_id])
        model.setdefault("runtime", "standard")
        model.setdefault("model_ref", model_id)
        dependency_available = bool(self._dependency_checker(model_id))
        is_runtime = self._is_runtime_model(model_id)
        url = self._resolve_url(model_id) if not is_runtime else None
        target = self._target_path(model_id, url)
        runtime_files = self._runtime_cache_files(model_id) if is_runtime else {}
        runtime_weights = runtime_files.get("model.safetensors") if runtime_files else None
        target_exists = Path(runtime_weights).is_file() if runtime_weights else target.is_file()
        target_size = Path(runtime_weights).stat().st_size if runtime_weights and Path(runtime_weights).is_file() else target.stat().st_size if target.is_file() else 0
        downloaded_bytes = target_size
        weights_available = target_exists and downloaded_bytes > 0
        state = dict(self._states.get(model_id, {}))
        state_status = str(state.get("status") or "")
        if state_status == "ready" and not dependency_available:
            status = "dependency_missing"
        else:
            status = state_status or ("dependency_missing" if not dependency_available else "ready" if weights_available else "not_downloaded" if is_runtime or url else "runtime_download")
        if status == "downloading":
            downloaded_bytes = int(state.get("downloaded_bytes") or downloaded_bytes)
        total_bytes = int(state.get("total_bytes") or 0)
        progress = int(state.get("progress") or (100 if weights_available else 0))
        model.update(
            {
                "available": dependency_available,
                "dependency_available": dependency_available,
                "download_supported": bool(model.get("model_ref")) if is_runtime else bool(url),
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

    def _download_runtime_model(self, model_id: str, cancel_event: threading.Event) -> None:
        model_ref = str(self._catalog[model_id].get("model_ref") or "").strip()
        try:
            huggingface = importlib.import_module("huggingface_hub")
            if cancel_event.is_set():
                raise _DownloadCancelled()
            self._set_state(model_id, progress=0, message="正在获取模型配置")
            huggingface.hf_hub_download(
                repo_id=model_ref,
                filename="config.json",
                cache_dir=str(self._runtime_cache_root),
            )
            self._set_state(model_id, progress=5, message="正在下载模型权重")
            if cancel_event.is_set():
                raise _DownloadCancelled()
            # Without `tqdm_class` the weight download — gigabytes, minutes — reports
            # nothing at all, and the UI sits on the 5% set above until the file is
            # already finished. See _byte_progress_sink.
            weights_path = huggingface.hf_hub_download(
                repo_id=model_ref,
                filename="model.safetensors",
                cache_dir=str(self._runtime_cache_root),
                tqdm_class=self._byte_progress_sink(model_id),
            )
            if cancel_event.is_set():
                raise _DownloadCancelled()
            weight_size = Path(weights_path).stat().st_size if Path(weights_path).is_file() else 0
            self._set_state(
                model_id,
                status="ready",
                downloaded_bytes=weight_size,
                total_bytes=weight_size,
                progress=100,
                message="模型已下载，可以开始听课",
                error=None,
            )
        except _DownloadCancelled:
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
            self._set_state(
                model_id,
                status="failed",
                message="模型下载失败，请检查网络后重试",
                error=str(exc),
            )
        finally:
            with self._lock:
                self._cancel_events.pop(model_id, None)

    def _set_state(self, model_id: str, **changes: object) -> None:
        with self._lock:
            state = dict(self._states.get(model_id, {}))
            state.update(changes)
            self._states[model_id] = state

    def _byte_progress_sink(self, model_id: str):
        """A `tqdm_class` that mirrors Hugging Face's byte counter into the model state.

        `hf_hub_download` has no callback of its own: the *only* way to hear about an
        in-progress download is to hand it a progress-bar class. Without one, the weight
        download — gigabytes, minutes, the single thing a first-run user waits for —
        publishes nothing, and the UI sat on the 5% marker until the file was already done.

        Two properties of the library shape this class:

        * Byte accounting lives on the instance, not read back from the inherited counter,
          because `tqdm.update()` returns early when the bar is disabled and leaves `n`
          frozen at `initial`. A bar that prints nothing must still report.
        * `disable=True` is forced. Hugging Face only injects `disable` for *its own*
          subclass; a custom class gets the vanilla default of `False`, which would stream
          a live terminal bar into stderr — inside the DMG, that is the server log file.
          The browser renders the progress; this object is only a byte counter wearing a
          progress bar's signature.

        Returns None when `tqdm` cannot be imported, which keeps the download working the
        way it did before. A machine without local runtime dependencies is not downloading
        weights in the first place.
        """
        try:
            from tqdm import tqdm as _Tqdm
        except ImportError:  # pragma: no cover - exercised in a core-only install
            return None

        manager = self

        class _StateProgress(_Tqdm):
            def __init__(self, *args, **kwargs):
                self._bytes_seen = int(kwargs.get("initial") or 0)
                self._total_bytes = int(kwargs.get("total") or 0)
                kwargs["disable"] = True
                super().__init__(*args, **kwargs)
                self._publish()

            def update(self, n=1):
                self._bytes_seen += int(n or 0)
                self._publish()
                return super().update(n)

            def _publish(self) -> None:
                seen = max(0, self._bytes_seen)
                total = self._total_bytes or int(getattr(self, "total", 0) or 0)
                if total <= 0:
                    manager._advance_download_state(model_id, downloaded_bytes=seen)
                    return
                # Weights own 5..99%: 5 is the "download started" marker set by the caller,
                # and 100 belongs to the caller that knows the file verified on disk.
                manager._advance_download_state(
                    model_id,
                    downloaded_bytes=seen,
                    total_bytes=total,
                    progress=5 + min(94, int(seen * 94 / total)),
                )

        return _StateProgress

    def _advance_download_state(self, model_id: str, **changes: object) -> None:
        """Merge a progress report, never letting it move backwards.

        A Xet-backed download builds one bar for network bytes and one for bytes written to
        disk, so two instances of the sink report the same model independently and their
        byte counts differ by buffering. Progress is monotonic while a download runs, so
        taking the maximum is both the right model and the fix for that flapping.
        """
        with self._lock:
            state = dict(self._states.get(model_id, {}))
            incoming = changes.get("progress")
            if incoming is not None and int(incoming) < int(state.get("progress") or 0):
                changes.pop("progress")
            incoming_bytes = changes.get("downloaded_bytes")
            if incoming_bytes is not None and int(incoming_bytes) < int(state.get("downloaded_bytes") or 0):
                changes.pop("downloaded_bytes")
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
