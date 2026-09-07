from typing import Callable, Optional

from ..config import AppConfig
from ..device import DeviceProfile, get_device_profile
from ..models import SessionConfig
from .base import ProviderError, TranscriptionProvider
from .cloud_transcription import CloudTranscriptionProvider
from .local_whisper import LocalWhisperProvider, local_model_available


class AutoFallbackProvider:
    """Start locally first, then use cloud when local loading is not viable."""

    name = "auto"
    model = None

    def __init__(self, local: TranscriptionProvider, cloud: TranscriptionProvider):
        self._local = local
        self._cloud = cloud
        self._active: Optional[TranscriptionProvider] = None
        self._local_error: Optional[Exception] = None

    def start(self, config: SessionConfig) -> None:
        try:
            self._local.start(config)
            self._active = self._local
            self.name = getattr(self._local, "name", "local")
            self.model = getattr(self._local, "model", None)
            return
        except Exception as exc:
            self._local_error = exc
            try:
                self._local.close()
            except Exception:
                pass

        try:
            self._cloud.start(config)
        except Exception as cloud_error:
            raise ProviderError(
                "AUTO_PROVIDER_FAILED",
                "本地模型无法启动，云端 provider 也不可用",
                "降低本地模型大小、检查云端配置，或单独选择可用模式",
            ) from cloud_error
        self._active = self._cloud
        self.name = getattr(self._cloud, "name", "cloud")
        self.model = getattr(self._cloud, "model", None)

    def push(self, audio):
        if self._active is None:
            raise ProviderError("AUTO_SESSION_NOT_STARTED", "自动 provider 尚未启动")
        return self._active.push(audio)

    def flush(self):
        if self._active is None:
            return []
        return self._active.flush()

    def close(self):
        if self._active is not None:
            self._active.close()
        self._active = None


class ProviderFactory:
    def __init__(
        self,
        config: AppConfig,
        local_available: Optional[Callable[[str], bool]] = None,
        device_profile: Optional[DeviceProfile] = None,
    ):
        self.config = config
        self._local_available = local_available or local_model_available
        self._device_profile = device_profile or get_device_profile()

    def create(self, session_config: SessionConfig) -> TranscriptionProvider:
        local_model = session_config.model or self.config.local_model
        if session_config.mode == "local":
            if not self._local_available(local_model):
                raise ProviderError(
                    "LOCAL_MODEL_UNAVAILABLE",
                    "本地模型依赖或模型不可用",
                    "请安装 requirements-local.txt，或切换到云端模式",
                )
            return LocalWhisperProvider(
                model_name=local_model,
                device_profile=self._device_profile,
            )

        if session_config.mode == "cloud":
            return self._create_cloud()

        if self._local_available(local_model):
            local_provider = LocalWhisperProvider(
                model_name=local_model,
                device_profile=self._device_profile,
            )
            if self.config.cloud_configured:
                return AutoFallbackProvider(local_provider, self._create_cloud())
            return local_provider
        if self.config.cloud_configured:
            return self._create_cloud()
        raise ProviderError(
            "NO_TRANSCRIPTION_PROVIDER",
            "没有可用的本地或云端转录 provider",
            "安装 requirements-local.txt，或配置云端转录环境变量",
        )

    def _create_cloud(self) -> CloudTranscriptionProvider:
        if not self.config.cloud_configured:
            raise ProviderError(
                "CLOUD_NOT_CONFIGURED",
                "云端转录尚未配置",
                "请设置 CLOUD_BASE_URL、CLOUD_API_KEY 和 CLOUD_TRANSCRIPTION_MODEL",
            )
        return CloudTranscriptionProvider(
            base_url=self.config.cloud_base_url,
            api_key=self.config.cloud_api_key,
            model=self.config.cloud_transcription_model,
            timeout_seconds=self.config.cloud_timeout_seconds,
        )
