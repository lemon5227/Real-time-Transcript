"""Platform-neutral client for the optional native speaker diarizer."""

from __future__ import annotations

import base64
import json
import os
import select
import shlex
import subprocess
import threading
from typing import List, Optional, Protocol, Sequence, Union

import numpy as np

from ..diarization import SpeakerTurn
from .base import ProviderError

Command = Union[str, Sequence[str]]


class SpeakerDiarizer(Protocol):
    name: str

    def start(self, sample_rate: int, variant: str = "fast") -> None:
        ...

    def push(self, audio: np.ndarray, start_ms: int) -> List[SpeakerTurn]:
        ...

    def flush(self) -> List[SpeakerTurn]:
        ...

    def close(self) -> None:
        ...


class NullSpeakerDiarizer:
    name = "disabled"

    def start(self, sample_rate: int, variant: str = "fast") -> None:
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
        variant: str = "fast",
        request_timeout_seconds: float = 12.0,
    ) -> None:
        self._command = self._normalize_command(command)
        self._variant = variant
        self._request_timeout_seconds = request_timeout_seconds
        self._process: Optional[subprocess.Popen[str]] = None
        self._lock = threading.RLock()

    def start(self, sample_rate: int, variant: str = "fast") -> None:
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
        ready, _, _ = select.select([stdout], [], [], self._request_timeout_seconds)
        if not ready:
            raise ProviderError(
                "DIARIZATION_TIMEOUT",
                "说话人识别响应超时",
                "关闭说话人识别后重试",
            )
        return stdout.readline().strip()

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


def create_diarizer(
    command: Command,
    *,
    enabled: bool,
    variant: str = "fast",
) -> SpeakerDiarizer:
    if not enabled or not command:
        return NullSpeakerDiarizer()
    return JsonlNemotronDiarizer(command, variant=variant)
