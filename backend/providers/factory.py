from typing import Callable, Optional

from ..config import AppConfig
from ..device import DeviceProfile, get_device_profile, runtime_for_profile
from ..models import SessionConfig
from .base import ProviderError, TranscriptionProvider
from .cloud_transcription import CloudTranscriptionProvider
from .local_whisper import LocalWhisperProvider, local_model_available
from .mlx_parakeet import (
    PARAKEET_MODEL_ID,
    PARAKEET_MODEL_REF,
    MlxParakeetProvider,
    mlx_runtime_available,
)


class AutoFallbackProvider:
    """Start locally first, then use cloud when local loading is not viable."""

    name = "auto"
    model = None
    requires_contiguous_audio = False

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
            self.requires_contiguous_audio = bool(
                getattr(self._local, "requires_contiguous_audio", False)
            )
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
        self.requires_contiguous_audio = bool(
            getattr(self._cloud, "requires_contiguous_audio", False)
        )

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
        self.requires_contiguous_audio = False


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
        if (
            session_config.mode == "auto"
            and runtime_for_profile(self._device_profile) == "mlx"
            and local_model != PARAKEET_MODEL_ID
        ):
            # Older preferences commonly contain "small". Auto mode must not
            # silently put an Apple Silicon Mac back on the CPU Whisper path.
            local_model = PARAKEET_MODEL_ID
        if session_config.mode == "local":
            return self._create_local(local_model)

        if session_config.mode == "cloud":
            return self._create_cloud()

        if self._local_is_available(local_model):
            local_provider = self._create_local(local_model)
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

    def _create_local(self, model_id: str) -> TranscriptionProvider:
        runtime = runtime_for_profile(self._device_profile)
        if runtime == "mlx":
            if model_id != PARAKEET_MODEL_ID:
                raise ProviderError(
                    "LOCAL_RUNTIME_MISMATCH",
                    "Apple Silicon 本地运行时使用 MLX Parakeet 模型",
                    "请选择 Parakeet TDT v3，中文或其他不支持语言请切换到云端",
                )
            if not self._local_is_available(model_id):
                raise ProviderError(
                    "LOCAL_MODEL_UNAVAILABLE",
                    "Mac MLX 转录依赖或模型不可用",
                    "请安装 requirements-mac.txt，或切换到云端模式",
                )
            return MlxParakeetProvider(
                PARAKEET_MODEL_REF,
                right_context=self.config.mlx_stream_right_context,
            )

        if model_id == PARAKEET_MODEL_ID:
            raise ProviderError(
                "LOCAL_RUNTIME_MISMATCH",
                "Parakeet MLX 模型只能在 Apple Silicon Mac 上运行",
                "在当前设备选择 Whisper 模型，或切换到云端",
            )
        if not self._local_is_available(model_id):
            raise ProviderError(
                "LOCAL_MODEL_UNAVAILABLE",
                "本地模型依赖或模型不可用",
                "请安装 requirements-local.txt，或切换到云端模式",
            )
        return LocalWhisperProvider(
            model_name=model_id,
            device_profile=self._device_profile,
        )

    def _local_is_available(self, model_id: str) -> bool:
        if model_id == PARAKEET_MODEL_ID:
            if self._local_available is not local_model_available:
                return self._local_available(model_id)
            return mlx_runtime_available()
        return self._local_available(model_id)

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
