"""Platform-neutral client for the optional native speaker diarizer."""

from __future__ import annotations

import base64
import json
import os
import queue
import select
import shlex
import subprocess
import threading
from typing import Callable, List, Optional, Protocol, Sequence, Union

import numpy as np

from ..diarization import SpeakerTurn
from .base import ProviderError

Command = Union[str, Sequence[str]]


class DiarizationPrepareError(RuntimeError):
    """The helper could not download or compile the speaker model weights."""

    def __init__(self, message: str, *, code: str = "DIARIZATION_PREPARE_FAILED") -> None:
        super().__init__(message)
        self.code = code


class SpeakerDiarizer(Protocol):
    name: str

    def start(self, sample_rate: int, variant: str = "low") -> None:
        ...

    def push(self, audio: np.ndarray, start_ms: int) -> List[SpeakerTurn]:
        ...

    def flush(self) -> List[SpeakerTurn]:
        ...

    def close(self) -> None:
        ...


class NullSpeakerDiarizer:
    name = "disabled"

    def start(self, sample_rate: int, variant: str = "low") -> None:
        del sample_rate, variant

    def push(self, audio: np.ndarray, start_ms: int) -> List[SpeakerTurn]:
        del audio, start_ms
        return []

    def flush(self) -> List[SpeakerTurn]:
        return []

    def close(self) -> None:
        return None


class JsonlNemotronDiarizer:
    """Talk to the macOS Swift/CoreML helper using one request per JSON line."""

    name = "nemotron-coreml"
    MAX_RESPONSE_BYTES = 2_000_000

    def __init__(
        self,
        command: Command,
        *,
        variant: str = "low",
        request_timeout_seconds: float = 12.0,
    ) -> None:
        self._command = self._normalize_command(command)
        self._variant = variant
        self._request_timeout_seconds = request_timeout_seconds
        self._process: Optional[subprocess.Popen[str]] = None
        self._lock = threading.RLock()

    def start(self, sample_rate: int, variant: str = "low") -> None:
        if sample_rate != 16000:
            raise ProviderError(
                "DIARIZATION_SAMPLE_RATE_UNSUPPORTED",
                "说话人识别需要 16kHz 音频",
                "让浏览器使用默认采样率后重试",
            )
        with self._lock:
            if self._process is not None:
                return
            if not self._command:
                raise ProviderError(
                    "DIARIZATION_HELPER_UNAVAILABLE",
                    "本机没有安装说话人识别组件",
                    "在设置中关闭说话人识别，字幕仍可正常使用",
                )
            try:
                self._process = subprocess.Popen(
                    self._command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=os.environ.copy(),
                )
            except OSError as exc:
                raise ProviderError(
                    "DIARIZATION_HELPER_UNAVAILABLE",
                    "说话人识别组件无法启动",
                    "在设置中关闭说话人识别，字幕仍可正常使用",
                ) from exc
            self._drain_stderr(self._process)
            try:
                response = self._request(
                    {
                        "type": "start",
                        "sample_rate": sample_rate,
                        "variant": variant or self._variant,
                    }
                )
                if response.get("type") != "ready":
                    raise ProviderError(
                        "DIARIZATION_HELPER_FAILED",
                        "说话人识别模型未能就绪",
                        "检查模型下载状态，或关闭说话人识别",
                    )
            except Exception:
                self.close()
                raise

    def push(self, audio: np.ndarray, start_ms: int) -> List[SpeakerTurn]:
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return []
        pcm = np.clip(samples, -1.0, 1.0)
        pcm = np.rint(pcm * 32767).astype("<i2", copy=False)
        response = self._request(
            {
                "type": "audio",
                "start_ms": max(0, int(start_ms)),
                "pcm16_base64": base64.b64encode(pcm.tobytes()).decode("ascii"),
            }
        )
        return self._turns_from_response(response)

    def flush(self) -> List[SpeakerTurn]:
        if self._process is None:
            return []
        return self._turns_from_response(self._request({"type": "flush"}))

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is None:
                return
            _terminate_helper(process)

    def _request(self, payload: dict) -> dict:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None or process.stdout is None:
                raise ProviderError(
                    "DIARIZATION_HELPER_FAILED",
                    "说话人识别组件已退出",
                    "字幕仍可继续使用；结束课堂后检查日志",
                )
            try:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()
                line = self._readline(process.stdout)
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise ProviderError(
                    "DIARIZATION_HELPER_FAILED",
                    "说话人识别组件通信失败",
                    "字幕仍可继续使用；结束课堂后检查日志",
                ) from exc
            if not line:
                raise ProviderError(
                    "DIARIZATION_HELPER_FAILED",
                    "说话人识别组件提前退出",
                    "字幕仍可继续使用；结束课堂后检查日志",
                )
            if len(line.encode("utf-8")) > self.MAX_RESPONSE_BYTES:
                raise ProviderError(
                    "DIARIZATION_PROTOCOL_ERROR",
                    "说话人识别返回内容过大",
                    "关闭说话人识别后重试",
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "DIARIZATION_PROTOCOL_ERROR",
                    "说话人识别返回了无效数据",
                    "关闭说话人识别后重试",
                ) from exc
            if not isinstance(response, dict):
                raise ProviderError(
                    "DIARIZATION_PROTOCOL_ERROR",
                    "说话人识别返回格式无效",
                    "关闭说话人识别后重试",
                )
            if response.get("type") == "error":
                raise ProviderError(
                    str(response.get("code") or "DIARIZATION_HELPER_FAILED"),
                    str(response.get("message") or "说话人识别失败"),
                    "关闭说话人识别后重试",
                )
            return response

    def _readline(self, stdout) -> str:
        try:
            return _read_line(stdout, self._request_timeout_seconds)
        except TimeoutError as exc:
            raise ProviderError(
                "DIARIZATION_TIMEOUT",
                "说话人识别响应超时",
                "关闭说话人识别后重试",
            ) from exc

    @staticmethod
    def _turns_from_response(response: dict) -> List[SpeakerTurn]:
        if response.get("type") not in {"speaker_turns", "flushed", "stopped"}:
            return []
        turns = response.get("turns") or []
        if not isinstance(turns, list):
            raise ProviderError(
                "DIARIZATION_PROTOCOL_ERROR",
                "说话人识别 turn 数据格式无效",
                "关闭说话人识别后重试",
            )
        result = []
        try:
            for item in turns:
                if not isinstance(item, dict):
                    raise ValueError("turn must be an object")
                result.append(
                    SpeakerTurn(
                        speaker_id=str(item["speaker_id"]),
                        start_ms=int(item["start_ms"]),
                        end_ms=int(item["end_ms"]),
                        confidence=float(item.get("confidence", 1.0)),
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "DIARIZATION_PROTOCOL_ERROR",
                "说话人识别 turn 数据无效",
                "关闭说话人识别后重试",
            ) from exc
        return result

    @staticmethod
    def _normalize_command(command: Command) -> List[str]:
        if isinstance(command, str):
            return shlex.split(command)
        return [str(item) for item in command]

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return

        def drain() -> None:
            for line in process.stderr:
                # Native logs belong in the Python session log, not stdout where
                # they would corrupt the JSONL protocol. Keep this bounded and
                # intentionally quiet here; the process exit is the useful signal.
                del line

        threading.Thread(target=drain, name="diarization-stderr", daemon=True).start()


def _read_line(stdout, timeout_seconds: float) -> str:
    ready, _, _ = select.select([stdout], [], [], timeout_seconds)
    if not ready:
        raise TimeoutError("no response line within the timeout")
    return stdout.readline().strip()


def _stream_lines(stdout, timeout_seconds: float):
    """Yield response lines with a stall timeout.

    ``select`` cannot be combined with ``readline`` here: the text wrapper may
    read several queued lines into its buffer, after which the file descriptor
    looks idle while complete lines are still waiting. A reader thread plus a
    queue keeps the timeout semantics without losing buffered lines.
    """
    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def pump() -> None:
        try:
            for line in stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, name="diarization-prepare-reader", daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise TimeoutError("no response line within the timeout") from exc
        if line is None:
            return
        yield line


def _terminate_helper(process: subprocess.Popen[str]) -> None:
    try:
        if process.poll() is None and process.stdin is not None:
            process.stdin.write(json.dumps({"type": "stop"}) + "\n")
            process.stdin.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    try:
        process.stdin.close() if process.stdin is not None else None
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)


def prepare_nemotron_models(
    command: Command,
    *,
    variant: str = "low",
    on_progress: Optional[Callable[[float], None]] = None,
    line_timeout_seconds: float = 300.0,
) -> None:
    """Ask the Swift helper to download and compile the preset weights.

    Unlike the session protocol (exactly one response per request) this request
    streams any number of `download_progress` lines until a final `ready` or
    `error`. It exists so the settings page can prepare the model before class
    without touching the live `start`/`audio`/`flush` path.
    """
    argv = JsonlNemotronDiarizer._normalize_command(command)
    if not argv:
        raise DiarizationPrepareError(
            "本机没有安装说话人识别组件",
            code="DIARIZATION_HELPER_UNAVAILABLE",
        )
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise DiarizationPrepareError(
            "说话人识别组件无法启动",
            code="DIARIZATION_HELPER_UNAVAILABLE",
        ) from exc
    JsonlNemotronDiarizer._drain_stderr(process)
    try:
        process.stdin.write(json.dumps({"type": "prepare", "variant": variant}) + "\n")
        process.stdin.flush()
        responses = _stream_lines(process.stdout, line_timeout_seconds)
        while True:
            try:
                line = next(responses).strip()
            except TimeoutError as exc:
                raise DiarizationPrepareError(
                    "模型下载超时，请检查网络后重试",
                    code="DIARIZATION_PREPARE_TIMEOUT",
                ) from exc
            except StopIteration:
                raise DiarizationPrepareError(
                    "说话人识别组件提前退出",
                    code="DIARIZATION_HELPER_FAILED",
                ) from None
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DiarizationPrepareError(
                    "说话人识别组件返回了无效数据",
                    code="DIARIZATION_PROTOCOL_ERROR",
                ) from exc
            if not isinstance(message, dict):
                raise DiarizationPrepareError(
                    "说话人识别组件返回格式无效",
                    code="DIARIZATION_PROTOCOL_ERROR",
                )
            kind = message.get("type")
            if kind == "download_progress":
                if on_progress is not None:
                    try:
                        fraction = float(message.get("fraction") or 0.0)
                    except (TypeError, ValueError):
                        fraction = 0.0
                    on_progress(max(0.0, min(1.0, fraction)))
                continue
            if kind == "ready":
                return
            if kind == "error":
                raise DiarizationPrepareError(
                    str(message.get("message") or "说话人模型下载失败"),
                    code=str(message.get("code") or "DIARIZATION_PREPARE_FAILED"),
                )
            raise DiarizationPrepareError(
                "说话人识别组件响应类型异常",
                code="DIARIZATION_PROTOCOL_ERROR",
            )
    finally:
        _terminate_helper(process)


def create_diarizer(
    command: Command,
    *,
    enabled: bool,
    variant: str = "low",
) -> SpeakerDiarizer:
    if not enabled or not command:
        return NullSpeakerDiarizer()
    return JsonlNemotronDiarizer(command, variant=variant)
