from typing import Callable, Optional

from ..config import AppConfig
from ..device import DeviceProfile, get_device_profile
from ..models import SessionConfig
from .base import ProviderError, TranscriptionProvider
from .cloud_transcription import CloudTranscriptionProvider
from .local_whisper import LocalWhisperProvider, local_model_available


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
            return LocalWhisperProvider(
                model_name=local_model,
                device_profile=self._device_profile,
            )
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
