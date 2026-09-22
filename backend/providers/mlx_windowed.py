"""Live Apple Silicon captions by re-decoding a sliding window in full context.

The streaming decoder in ``parakeet_mlx`` keeps the encoder's *local* attention
and refuses to confirm the newest frames. On clean synthetic speech that is
fine, but on a real accented lecture it collapses into word salad -- measured on
a 40s classroom excerpt it produced unusable text where the full-context path
stayed readable. It is also slower than real time: one second of audio took
1.5s to decode, so the caption falls further behind the longer the lecture runs.

This provider instead re-decodes a window of recent audio with the same
full-context path the post-class refine pass uses. The window *ends at the live
edge*, so the window length buys left context -- which is what makes the text
readable -- without adding latency. Latency is bounded by the decode time plus
the hop between decodes.

The window also starts short and grows into its full length, so the first caption
does not wait for a full window of audio to accumulate. The cold start is then set
by the shortest window that produces a sentence past the head guard (8s measured
on the real lecture) rather than by the configured window length.

Consecutive windows overlap by design, and ``SegmentMerger`` already knows how
to drop an exact repeat and to replace a partial decode with the more complete
one, re-using the original id so the client updates the caption in place.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional

import numpy as np

from ..logging_setup import get_logger
from ..models import SessionConfig, TranscriptSegment
from .base import ProviderError
from .mlx_parakeet import PARAKEET_LANGUAGES, MlxModelCache, resolve_local_model_source

logger = get_logger("mlx.windowed")

SAMPLE_RATE = 16000

# 15-20s windows were the range where the decode stayed readable on the real
# lecture recording; below ~12s the text loses its opening context and can come
# back empty. The hop is what the user feels as latency, so it stays small.
#
# The sweep that would justify changing 18 is in ``docs/LATENCY.md``: 12s and 24s
# both drop content against the refine pass, 30s scores best on text similarity
# only because it repeats whole sentences, and 18s is the one whose word count
# lands on the reference. Do not retune this on similarity alone.
DEFAULT_WINDOW_SECONDS = 18.0
DEFAULT_HOP_SECONDS = 2.0
MIN_WINDOW_SECONDS = 4.0

# The window length is a *left-context* budget, and left context is free in
# steady state: the window ends at the live edge, so a longer window does not
# push the caption further behind the speaker. It used to cost the cold start,
# because the provider waited for a full window before decoding anything -- 18s
# of silence at the start of every lecture, and raising the window for better
# text meant waiting even longer.
#
# Nothing about the design requires that wait. A window that ends at the live
# edge is already "everything heard so far, capped at the window length", so it
# starts short and grows into the full length by itself; the gate in ``push()``
# was the only thing forcing it to wait. Measured on the real lecture, the cold
# start is then set by the shortest window that yields a sentence starting past
# the head guard, which is 8s -- not by the window length.

# The first seconds of a window are decoded without left context, which is where
# the garbled openings came from ("the on the concrete..."). A sentence is only
# published once the window has seen it start past this guard, so it always has
# real context behind it. Audio inside the guard is not lost: the window keeps
# sliding, so the same speech is re-decoded from a later, better-informed window.
#
# The one place that reasoning does not hold is the start of the recording: the
# window cannot slide left of zero, so the first ``head_guard`` seconds of a
# session are inside the guard of every decode and are never published. That is
# a deliberate trade -- those seconds genuinely have no left context, so the
# alternative is showing the garbled opening the guard exists to hide -- but it
# is a real loss of the first sentence, not the "nothing is lost" the sentence
# above describes. It is bounded by this constant and happens once per session.
DEFAULT_HEAD_GUARD_SECONDS = 4.0


class MlxWindowedParakeetProvider:
    name = "mlx"
    # The manager feeds this provider contiguous one-second chunks and lets it
    # own the timeline, exactly as it does for the streaming decoder.
    requires_contiguous_audio = True
    model: Optional[str]

    def __init__(
        self,
        model_ref: str,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        hop_seconds: float = DEFAULT_HOP_SECONDS,
        head_guard_seconds: float = DEFAULT_HEAD_GUARD_SECONDS,
        model_cache: Optional[MlxModelCache] = None,
        transcribe: Optional[Callable[[np.ndarray], object]] = None,
    ):
        if window_seconds < MIN_WINDOW_SECONDS:
            raise ValueError("window_seconds must be at least %.1fs" % MIN_WINDOW_SECONDS)
        if hop_seconds <= 0:
            raise ValueError("hop_seconds must be positive")
        if head_guard_seconds < 0 or head_guard_seconds >= window_seconds:
            raise ValueError("head_guard_seconds must be inside the window")
        self.model = model_ref
        self._window_samples = max(1, round(window_seconds * SAMPLE_RATE))
        self._hop_samples = max(1, round(hop_seconds * SAMPLE_RATE))
        self._head_guard_samples = round(head_guard_seconds * SAMPLE_RATE)
        # The first decode must already be able to publish something, otherwise
        # it is pure waste: the head guard would discard every sentence in it.
        # So the window opens at the guard plus one hop, or at the smallest
        # window the decoder can work with, whichever is longer. It then grows
        # to the full window on its own -- see the module docstring.
        self._first_window_samples = min(
            self._window_samples,
            max(
                max(1, round(MIN_WINDOW_SECONDS * SAMPLE_RATE)),
                self._head_guard_samples + self._hop_samples,
            ),
        )
        self._model_cache = model_cache
        self._model_cache_acquired = False
        self._transcribe_override = transcribe
        self._model = None
        self._config: Optional[SessionConfig] = None
        self._buffer = np.empty(0, dtype=np.float32)
        self._total_samples = 0
        self._decoded_samples = 0
        self._segment_number = 0

    @property
    def window_seconds(self) -> float:
        return self._window_samples / SAMPLE_RATE

    @property
    def hop_seconds(self) -> float:
        return self._hop_samples / SAMPLE_RATE

    @property
    def first_window_seconds(self) -> float:
        """How much audio the first decode waits for -- the cold start."""
        return self._first_window_samples / SAMPLE_RATE

    def start(self, config: SessionConfig) -> None:
        if config.language not in PARAKEET_LANGUAGES and config.language != "auto":
            raise ProviderError(
                "MLX_LANGUAGE_UNSUPPORTED",
                "Mac MLX 版本当前主要支持英语和欧洲语言",
                "将讲课语言改为 English，或切换到云端",
            )
        self._config = config
        self._buffer = np.empty(0, dtype=np.float32)
        self._total_samples = 0
        self._decoded_samples = 0
        self._segment_number = 0
        try:
            if self._model_cache is not None:
                self._model = self._model_cache.acquire(self.model)
                self._model_cache_acquired = True
            else:
                from parakeet_mlx import from_pretrained

                self._model = from_pretrained(
                    resolve_local_model_source(self.model) or self.model
                )
            if self._model is None:
                raise ImportError("parakeet_mlx returned no model")
        except ProviderError:
            self._release_model_cache()
            raise
        except Exception as exc:
            self._release_model_cache()
            logger.exception("MLX 模型加载失败 model=%s 原因=%r", self.model, exc)
            raise ProviderError(
                "MLX_RUNTIME_UNAVAILABLE",
                "Mac MLX 本地转录依赖未安装或模型无法加载",
                "在 Apple Silicon Mac 安装 requirements-mac.txt，或切换到云端",
            ) from exc
        logger.info(
            "MLX 滑窗解码已就绪 model=%s 窗口=%.1fs 首窗=%.1fs 步长=%.1fs 头部保护=%.1fs",
            self.model,
            self.window_seconds,
            self.first_window_seconds,
            self.hop_seconds,
            self._head_guard_samples / SAMPLE_RATE,
        )

    def push(self, audio: np.ndarray) -> List[TranscriptSegment]:
        if self._model is None or self._config is None:
            raise ProviderError("MLX_SESSION_NOT_STARTED", "Mac MLX provider 尚未启动")
        chunk = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not chunk.size:
            return []
        self._buffer = np.concatenate((self._buffer, chunk))
        self._total_samples += int(chunk.size)
        # The gate is the *first* window, not the full one, so the first caption
        # does not wait for a full window of audio. The decode still uses up to
        # the full window; during the opening that is simply all the audio there
        # is, and the window grows into its full length as the lecture runs.
        if self._total_samples < self._first_window_samples:
            return []
        if self._total_samples - self._decoded_samples < self._hop_samples:
            return []
        return self._decode_window()

    def flush(self) -> List[TranscriptSegment]:
        if self._model is None or self._config is None:
            return []
        if self._total_samples <= 0 or self._total_samples <= self._decoded_samples:
            return []
        # The lecture is over, so the tail no longer needs the head guard: the
        # last few seconds would otherwise never be published.
        return self._decode_window(head_guard_samples=0)

    def close(self) -> None:
        self._release_model_cache()
        self._model = None
        self._config = None
        self._buffer = np.empty(0, dtype=np.float32)

    def _decode_window(
        self, head_guard_samples: Optional[int] = None
    ) -> List[TranscriptSegment]:
        guard = self._head_guard_samples if head_guard_samples is None else head_guard_samples
        # The window ends at the live edge and is at most the configured window
        # long. Early in a session that is all the audio there is, which is what
        # lets the first caption arrive before a full window has accumulated.
        window = self._buffer[-self._window_samples :]
        window_start = self._total_samples - int(window.size)
        started = time.monotonic()
        try:
            result = self._transcribe(window)
        except ProviderError:
            raise
        except Exception as exc:
            logger.exception("MLX 滑窗推理失败 model=%s", self.model)
            raise ProviderError(
                "MLX_INFERENCE_FAILED",
                "Mac MLX 本地推理失败",
                "降低系统负载、检查 requirements-mac.txt，或切换到云端",
            ) from exc
        decode_seconds = time.monotonic() - started
        self._decoded_samples = self._total_samples
        self._trim_buffer()
        segments = self._segments_from_result(result, window_start, guard)
        logger.debug(
            "滑窗解码 音频=%.2fs 解码=%.2fs 窗口起点=%.2fs 输出=%d 段",
            window.size / SAMPLE_RATE,
            decode_seconds,
            window_start / SAMPLE_RATE,
            len(segments),
        )
        return segments

    def _transcribe(self, window: np.ndarray) -> object:
        if self._transcribe_override is not None:
            return self._transcribe_override(window)
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        mel = get_logmel(mx.array(window), self._model.preprocessor_config)
        return self._model.generate(mel)[0]

    def _segments_from_result(
        self, result: object, window_start_samples: int, head_guard_samples: int
    ) -> List[TranscriptSegment]:
        guard_ms = round(head_guard_samples * 1000 / SAMPLE_RATE)
        segments: List[TranscriptSegment] = []
        for sentence in getattr(result, "sentences", None) or []:
            text = str(getattr(sentence, "text", "") or "").strip()
            if not text:
                continue
            start_seconds = getattr(sentence, "start", 0.0)
            # The guard is measured inside the window, so compare it against the
            # sentence's own offset rather than its absolute timeline position.
            if self._to_ms(0, start_seconds) < guard_ms:
                # Decoded without enough left context; a later window will see
                # this same speech past the guard and publish it properly.
                continue
            start_ms = self._to_ms(window_start_samples, start_seconds)
            end_ms = self._to_ms(window_start_samples, getattr(sentence, "end", 0.0))
            self._segment_number += 1
            segments.append(
                TranscriptSegment(
                    id="mlx-w-%d" % self._segment_number,
                    text=text,
                    start_ms=start_ms,
                    end_ms=max(start_ms + 1, end_ms),
                    is_final=True,
                )
            )
        return segments

    @staticmethod
    def _to_ms(offset_samples: int, seconds: float) -> int:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        if not np.isfinite(value):
            value = 0.0
        return max(0, round((offset_samples / SAMPLE_RATE + value) * 1000))

    def _trim_buffer(self) -> None:
        """Keep only the audio a future window can still reach."""
        if self._buffer.size > self._window_samples:
            self._buffer = self._buffer[-self._window_samples :].copy()

    def _release_model_cache(self) -> None:
        if not self._model_cache_acquired or self._model_cache is None:
            return
        self._model_cache.release(self.model)
        self._model_cache_acquired = False
