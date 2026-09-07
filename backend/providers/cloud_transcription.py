import io
import wave
from typing import List, Optional

import numpy as np

try:
    import requests
except ImportError:  # pragma: no cover - exercised in a core-only install
    requests = None

from ..models import SessionConfig, TranscriptSegment
from .base import ProviderError


class CloudTranscriptionProvider:
    name = "cloud"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._config: Optional[SessionConfig] = None
        self._segment_number = 0

    def start(self, config: SessionConfig) -> None:
        if requests is None:
            raise ProviderError(
                "CLOUD_DEPENDENCY_MISSING",
                "云端模式缺少 requests 依赖",
                "请安装 requirements-cloud.txt",
            )
        if not self.base_url or not self.api_key or not self.model:
            raise ProviderError(
                "CLOUD_NOT_CONFIGURED",
                "云端转录尚未配置",
                "请设置 CLOUD_BASE_URL、CLOUD_API_KEY 和 CLOUD_TRANSCRIPTION_MODEL",
            )
        self._config = config

    @staticmethod
    def _encode_wav(audio: np.ndarray) -> bytes:
        pcm = np.clip(np.asarray(audio, dtype=np.float32), -1, 1)
        pcm16 = (pcm * 32767).astype("<i2").tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm16)
        return output.getvalue()

    def push(self, audio: np.ndarray) -> List[TranscriptSegment]:
        if self._config is None:
            raise ProviderError("CLOUD_SESSION_NOT_STARTED", "云端 provider 尚未启动")
        if requests is None:
            raise ProviderError("CLOUD_DEPENDENCY_MISSING", "云端模式缺少 requests 依赖")

        url = self.base_url + "/audio/transcriptions"
        headers = {"Authorization": "Bearer " + self.api_key}
        files = {"file": ("audio.wav", self._encode_wav(audio), "audio/wav")}
        data = {
            "model": self.model,
            "language": self._config.language,
            "response_format": "json",
        }
        try:
            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise ProviderError(
                "CLOUD_TIMEOUT", "云端转录请求超时", "检查网络，或切换到本地模式"
            ) from exc
        except requests.ConnectionError as exc:
            raise ProviderError(
                "CLOUD_NETWORK_ERROR", "无法连接云端转录服务", "检查网络或云端地址"
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError("CLOUD_REQUEST_FAILED", "云端转录请求失败") from exc

        if response.status_code in {401, 403}:
            raise ProviderError(
                "CLOUD_AUTH_FAILED", "云端鉴权失败", "检查 CLOUD_API_KEY 和服务地址"
            )
        if response.status_code == 429:
            raise ProviderError(
                "CLOUD_RATE_LIMITED", "云端服务限流", "稍后重试或切换到本地模式"
            )
        if response.status_code >= 400:
            raise ProviderError("CLOUD_REQUEST_FAILED", "云端服务返回错误")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise ProviderError("CLOUD_INVALID_RESPONSE", "云端返回了无效 JSON") from exc

        text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
        if not text:
            raise ProviderError("CLOUD_INVALID_RESPONSE", "云端响应中没有字幕文本")

        duration_ms = round(len(audio) * 1000 / 16000)
        self._segment_number += 1
        return [
            TranscriptSegment(
                id="cloud-%d" % self._segment_number,
                text=text,
                start_ms=0,
                end_ms=max(1, duration_ms),
                is_final=True,
            )
        ]

    def flush(self) -> List[TranscriptSegment]:
        return []

    def close(self) -> None:
        self._config = None
