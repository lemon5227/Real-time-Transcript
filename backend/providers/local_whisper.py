import importlib.util
from typing import Callable, List, Optional

import numpy as np

from ..device import DeviceProfile, get_device_profile
from ..models import SessionConfig, TranscriptSegment
from .base import ProviderError


def local_model_available(_model_name: str) -> bool:
    return bool(
        importlib.util.find_spec("faster_whisper")
        or importlib.util.find_spec("whisper")
    )


class LocalWhisperProvider:
    name = "local"

    def __init__(
        self,
        model_name: str,
        device: Optional[str] = None,
        loader: Optional[Callable[..., object]] = None,
        device_profile: Optional[DeviceProfile] = None,
    ):
        self.model = model_name
        self.device = device or (device_profile or get_device_profile()).device
        self._loader = loader
        self._model = None
        self._backend = None
        self._config: Optional[SessionConfig] = None
        self._segment_number = 0

    def start(self, config: SessionConfig) -> None:
        self._config = config
        try:
            if self._loader is not None:
                self._model = self._loader(
                    model_name=self.model,
                    device=self.device,
                    compute_type=self._compute_type(),
                )
                self._backend = "fake" if self._model is not None else None
            elif self.model == "distil-small.en" and self.device != "cuda":
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model,
                    device="cpu",
                    compute_type="int8",
                )
                self._backend = "faster-whisper"
            elif self.device == "mps":
                import whisper

                self._model = whisper.load_model(self.model, device="mps")
                self._backend = "whisper"
            else:
                try:
                    from faster_whisper import WhisperModel

                    self._model = WhisperModel(
                        self.model,
                        device=self.device if self.device == "cuda" else "cpu",
                        compute_type=self._compute_type(),
                    )
                    self._backend = "faster-whisper"
                except ImportError:
                    import whisper

                    self._model = whisper.load_model(self.model, device=self.device)
                    self._backend = "whisper"
        except ImportError as exc:
            raise ProviderError(
                "LOCAL_MODEL_UNAVAILABLE",
                "本地转录依赖未安装",
                "请安装 requirements-local.txt，或切换到云端模式",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                "LOCAL_MODEL_LOAD_FAILED",
                "本地模型加载失败",
                "请降低模型大小、检查磁盘空间，或切换到云端模式",
            ) from exc

        if self._model is None:
            raise ProviderError(
                "LOCAL_MODEL_UNAVAILABLE",
                "本地模型不可用",
                "请安装本地模型依赖，或切换到云端模式",
            )

    def _compute_type(self) -> str:
        return "float16" if self.device == "cuda" else "int8"

    def push(self, audio: np.ndarray) -> List[TranscriptSegment]:
        if self._model is None or self._config is None:
            raise ProviderError("LOCAL_SESSION_NOT_STARTED", "本地 provider 尚未启动")
        try:
            if self._backend == "fake":
                raw_segments = self._model.transcribe(audio, language=self._config.language)
                if isinstance(raw_segments, dict):
                    text = raw_segments.get("text", "")
                    raw_segments = [{"text": text, "start": 0.0, "end": len(audio) / 16000}]
                else:
                    raw_segments = list(raw_segments)
            elif self._backend == "faster-whisper":
                raw_segments, _ = self._model.transcribe(
                    audio,
                    language=self._config.language,
                    beam_size=5,
                    vad_filter=self._config.enable_vad,
                )
                raw_segments = list(raw_segments)
            else:
                result = self._model.transcribe(
                    audio,
                    language=self._config.language,
                    # MPS float16 can produce NaN logits on Apple Silicon for
                    # some Whisper checkpoints; CUDA is the only safe fp16 path.
                    fp16=self.device == "cuda",
                )
                raw_segments = result.get("segments", [])
        except Exception as exc:
            raise ProviderError(
                "LOCAL_INFERENCE_FAILED",
                "本地模型推理失败",
                "请降低模型大小或切换到云端模式",
            ) from exc

        result: List[TranscriptSegment] = []
        for raw in raw_segments:
            if isinstance(raw, dict):
                text = str(raw.get("text", "")).strip()
                start = float(raw.get("start", 0.0))
                end = float(raw.get("end", start))
            else:
                text = str(getattr(raw, "text", "")).strip()
                start = float(getattr(raw, "start", 0.0))
                end = float(getattr(raw, "end", start))
            if not text:
                continue
            self._segment_number += 1
            result.append(
                TranscriptSegment(
                    id="local-%d" % self._segment_number,
                    text=text,
                    start_ms=max(0, round(start * 1000)),
                    end_ms=max(round(start * 1000), round(end * 1000)),
                    is_final=True,
                )
            )
        return result

    def flush(self) -> List[TranscriptSegment]:
        return []

    def close(self) -> None:
        self._model = None
        self._config = None
