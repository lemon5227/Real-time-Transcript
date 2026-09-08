"""Apple Silicon MLX provider backed by the Parakeet TDT streaming model."""

from __future__ import annotations

import importlib.util
from typing import Callable, List, Optional, Set, Tuple

import numpy as np

from ..models import SessionConfig, TranscriptSegment
from .base import ProviderError

PARAKEET_LANGUAGES = {
    "bg",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "fi",
    "fr",
    "hr",
    "hu",
    "it",
    "lt",
    "lv",
    "mt",
    "nl",
    "pl",
    "pt",
    "ro",
    "ru",
    "sk",
    "sl",
    "sv",
    "uk",
}
PARAKEET_MODEL_ID = "parakeet-tdt-0.6b-v3"
PARAKEET_MODEL_REF = "mlx-community/parakeet-tdt-0.6b-v3"


def mlx_runtime_available() -> bool:
    try:
        return importlib.util.find_spec("parakeet_mlx") is not None
    except (ImportError, ValueError):
        return False


class MlxParakeetProvider:
    name = "mlx"
    requires_contiguous_audio = True

    def __init__(
        self,
        model_ref: str,
        loader: Optional[Callable[..., object]] = None,
    ):
        self.model = model_ref
        self._loader = loader
        self._model = None
        self._stream = None
        self._audio_converter = None
        self._config: Optional[SessionConfig] = None
        self._audio_samples = 0
        self._segment_number = 0
        self._emitted: Set[Tuple[int, int, str]] = set()
        self._last_draft = ""

    def start(self, config: SessionConfig) -> None:
        self._config = config
        if config.language not in PARAKEET_LANGUAGES and config.language != "auto":
            raise ProviderError(
                "MLX_LANGUAGE_UNSUPPORTED",
                "Mac MLX 版本当前主要支持英语和欧洲语言",
                "将讲课语言改为 English，或切换到云端",
            )
        try:
            if self._loader is not None:
                self._model = self._loader(model_ref=self.model)
            else:
                import mlx.core as mx
                from parakeet_mlx import from_pretrained

                self._audio_converter = mx.array
                self._model = from_pretrained(self.model)
            if self._model is None:
                raise ImportError("parakeet_mlx returned no model")
            stream = self._model.transcribe_stream(
                context_size=(256, 256),
                keep_original_attention=False,
            )
            self._stream = stream.__enter__()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                "MLX_RUNTIME_UNAVAILABLE",
                "Mac MLX 本地转录依赖未安装或模型无法加载",
                "在 Apple Silicon Mac 安装 requirements-mac.txt，或切换到云端",
            ) from exc

    def push(self, audio: np.ndarray) -> List[TranscriptSegment]:
        if self._stream is None or self._config is None:
            raise ProviderError("MLX_SESSION_NOT_STARTED", "Mac MLX provider 尚未启动")
        chunk = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not chunk.size:
            return []
        window_start_ms = round(self._audio_samples * 1000 / 16000)
        try:
            runtime_audio = self._audio_converter(chunk) if self._audio_converter else chunk
            self._stream.add_audio(runtime_audio)
            self._audio_samples += int(chunk.size)
            result = self._stream.result
        except Exception as exc:
            raise ProviderError(
                "MLX_INFERENCE_FAILED",
                "Mac MLX 本地推理失败",
                "降低系统负载、检查 requirements-mac.txt，或切换到云端",
            ) from exc

        segments = self._new_sentence_segments(result, window_start_ms)
        draft = self._draft_text(result)
        if draft and draft != self._last_draft:
            self._last_draft = draft
            segments.append(
                TranscriptSegment(
                    id="mlx-draft-%d" % self._audio_samples,
                    text=draft,
                    start_ms=0,
                    end_ms=max(1, round(chunk.size * 1000 / 16000)),
                    is_final=False,
                )
            )
        return segments

    def _new_sentence_segments(self, result: object, window_start_ms: int) -> List[TranscriptSegment]:
        output: List[TranscriptSegment] = []
        for sentence in getattr(result, "sentences", None) or []:
            text = str(getattr(sentence, "text", "") or "").strip()
            if not text:
                continue
            start = float(getattr(sentence, "start", 0.0) or 0.0)
            end = float(getattr(sentence, "end", start) or start)
            key = (round(start * 1000), round(end * 1000), text)
            if key in self._emitted:
                continue
            self._emitted.add(key)
            start_ms = max(0, round(start * 1000) - window_start_ms)
            end_ms = max(start_ms, round(end * 1000) - window_start_ms)
            self._segment_number += 1
            output.append(
                TranscriptSegment(
                    id="mlx-%d" % self._segment_number,
                    text=text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    is_final=True,
                )
            )
        return output

    def _draft_text(self, result: object) -> str:
        full_text = str(getattr(result, "text", "") or "").strip()
        if not full_text:
            return ""
        final_text = "".join(
            str(getattr(sentence, "text", "") or "").strip()
            for sentence in getattr(result, "sentences", None) or []
            if str(getattr(sentence, "text", "") or "").strip()
        ).strip()
        if final_text and full_text.startswith(final_text):
            return full_text[len(final_text):].strip()
        return full_text if not final_text else ""

    def flush(self) -> List[TranscriptSegment]:
        if self._stream is None:
            return []
        return self._new_sentence_segments(self._stream.result, 0)

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.__exit__(None, None, None)
            except Exception:
                pass
        self._model = None
        self._config = None
