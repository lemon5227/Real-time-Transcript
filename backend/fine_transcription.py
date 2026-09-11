"""Background batch transcription for saved post-class recordings."""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

MAX_AUDIO_BYTES = 512 * 1024 * 1024
REFINE_CHUNK_DURATION_SECONDS = 600.0
REFINE_OVERLAP_SECONDS = 15.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


class FineTranscriptionManager:
    """Run a full-context Parakeet pass without blocking Flask requests."""

    def __init__(
        self,
        model_cache: object,
        *,
        max_audio_bytes: int = MAX_AUDIO_BYTES,
        transcribe: Optional[Callable[..., object]] = None,
    ):
        self._model_cache = model_cache
        self._max_audio_bytes = max_audio_bytes
        self._transcribe = transcribe
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def start(self, audio_bytes: bytes, suffix: str, language: str, model_ref: str) -> Dict[str, Any]:
        if not audio_bytes:
            raise ValueError("audio must not be empty")
        if len(audio_bytes) > self._max_audio_bytes:
            raise ValueError("audio file is too large")

        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "model": model_ref,
            "language": language or "en",
            "segments": [],
            "createdAt": _now(),
            "startedAt": None,
            "completedAt": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(
            target=self._run,
            args=(job_id, bytes(audio_bytes), suffix or ".webm", language or "en", model_ref),
            name="fine-transcription-%s" % job_id[:8],
            daemon=True,
        ).start()
        return self.get(job_id) or job

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(changes)

    def _run(self, job_id: str, audio_bytes: bytes, suffix: str, language: str, model_ref: str) -> None:
        temporary_path: Optional[str] = None
        acquired = False
        try:
            extension = Path(suffix).suffix or ".webm"
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=extension, prefix="echonote-refine-", delete=False
            ) as temporary:
                temporary.write(audio_bytes)
                temporary_path = temporary.name

            self._update(job_id, status="processing", progress=0, startedAt=_now())
            if self._transcribe is not None:
                result = self._transcribe(
                    temporary_path,
                    chunk_duration=REFINE_CHUNK_DURATION_SECONDS,
                    overlap_duration=REFINE_OVERLAP_SECONDS,
                    chunk_callback=lambda *args, **kwargs: self._progress(job_id, *args, **kwargs),
                    language=language,
                )
            else:
                model = self._model_cache.acquire(model_ref)
                acquired = True
                try:
                    result = model.transcribe(
                        temporary_path,
                        chunk_duration=REFINE_CHUNK_DURATION_SECONDS,
                        overlap_duration=REFINE_OVERLAP_SECONDS,
                        chunk_callback=lambda *args, **kwargs: self._progress(job_id, *args, **kwargs),
                    )
                finally:
                    self._model_cache.release(model_ref)
                    acquired = False

            segments = self._segments_from_result(result)
            self._update(
                job_id,
                status="ready",
                progress=100,
                segments=segments,
                completedAt=_now(),
            )
        except Exception:
            if acquired:
                try:
                    self._model_cache.release(model_ref)
                except Exception:
                    pass
            self._update(
                job_id,
                status="failed",
                error={
                    "code": "FINE_TRANSCRIPTION_FAILED",
                    "message": "精细转录未完成",
                    "action": "确认已安装 Mac MLX 依赖和模型后重试",
                },
                completedAt=_now(),
            )
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def _progress(self, job_id: str, *args: object, **kwargs: object) -> None:
        current = kwargs.get("current") or kwargs.get("position")
        total = kwargs.get("total") or kwargs.get("total_position")
        if current is None and args:
            current = args[0]
        if total is None and len(args) > 1:
            total = args[1]
        try:
            current_number = float(current)
            total_number = float(total)
            progress = int(max(0, min(99, current_number / total_number * 100))) if total_number else 0
        except (TypeError, ValueError, ZeroDivisionError):
            progress = 0
        self._update(job_id, progress=progress)

    @staticmethod
    def _segments_from_result(result: object) -> list[Dict[str, Any]]:
        segments = []
        for index, sentence in enumerate(_value(result, "sentences", []) or []):
            text = str(_value(sentence, "text", "") or "").strip()
            if not text:
                continue
            try:
                start_ms = max(0, round(float(_value(sentence, "start", 0)) * 1000))
                end_ms = max(start_ms, round(float(_value(sentence, "end", 0)) * 1000))
            except (TypeError, ValueError):
                continue
            segments.append(
                {
                    "id": "refined-%d" % index,
                    "text": text,
                    "startMs": start_ms,
                    "endMs": end_ms,
                    "isFinal": True,
                }
            )
        return segments
