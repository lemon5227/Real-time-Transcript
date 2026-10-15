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

Consecutive windows overlap by design, but a decode's last sentence is only a
hypothesis: the next window may rewrite it. Keep that sentence as a stable-id
draft in the live feed, promote it when a following sentence appears, and never
re-emit already committed history from later windows.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from difflib import SequenceMatcher
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
# The guard only applies after the window starts inside the recording. At the
# true beginning there is no earlier speech to provide as context, so applying
# it there would permanently discard the opening sentence. Keep decoding from
# time zero unguarded; its trailing sentence remains a revisable draft until a
# later window supplies more context.
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
        self._committed_until_ms = 0
        self._live_segment: Optional[TranscriptSegment] = None

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
        self._committed_until_ms = 0
        self._live_segment = None
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
        if self._total_samples <= 0:
            return []
        if self._total_samples > self._decoded_samples:
            # The lecture is over, so the tail no longer needs the head guard:
            # the last few seconds would otherwise never be published.
            return self._decode_window(head_guard_samples=0, final=True)
        if self._live_segment is None:
            return []
        # A stop may arrive before another model hop. The last visible draft is
        # still useful and should become part of the saved lecture.
        final_segment = replace(self._live_segment, is_final=True)
        self._live_segment = None
        self._committed_until_ms = max(self._committed_until_ms, final_segment.end_ms)
        return [final_segment]

    def close(self) -> None:
        self._release_model_cache()
        self._model = None
        self._config = None
        self._buffer = np.empty(0, dtype=np.float32)
        self._live_segment = None

    def _decode_window(
        self, head_guard_samples: Optional[int] = None, final: bool = False
    ) -> List[TranscriptSegment]:
        # The window ends at the live edge and is at most the configured window
        # long. Early in a session that is all the audio there is, which is what
        # lets the first caption arrive before a full window has accumulated.
        window = self._buffer[-self._window_samples :]
        window_start = self._total_samples - int(window.size)
        if head_guard_samples is None:
            guard = 0 if window_start == 0 else self._head_guard_samples
        else:
            guard = head_guard_samples
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
        segments = self._stabilize_segments(segments, final=final)
        logger.debug(
            "滑窗解码 音频=%.2fs 解码=%.2fs 窗口起点=%.2fs 输出=%d 段",
            window.size / SAMPLE_RATE,
            decode_seconds,
            window_start / SAMPLE_RATE,
            len(segments),
        )
        return segments

    def _stabilize_segments(
        self, candidates: List[TranscriptSegment], *, final: bool
    ) -> List[TranscriptSegment]:
        """Publish only stable history; keep the newest decoded sentence live.

        A full-context decode may still revise its trailing sentence on the next
        hop. Treating every decode as final caused the merger to rewrite saved
        history with these temporary hypotheses. The provider already has the
        full rolling result, so it can commit each sentence once a later one
        appears and ignore old sentences on subsequent re-decodes.
        """
        candidates = sorted(candidates, key=lambda item: (item.start_ms, item.end_ms))
        if not candidates:
            if final and self._live_segment is not None:
                completed = replace(self._live_segment, is_final=True)
                self._live_segment = None
                self._committed_until_ms = max(self._committed_until_ms, completed.end_ms)
                return [completed]
            return []

        previous_live = self._live_segment
        matched_index = self._match_live_segment(previous_live, candidates)
        emitted: List[TranscriptSegment] = []

        # If a new decode no longer contains the active sentence, freeze the
        # last version the student actually saw before considering new speech.
        if previous_live is not None and matched_index is None:
            emitted.append(replace(previous_live, is_final=True))
            self._committed_until_ms = max(
                self._committed_until_ms, previous_live.end_ms
            )
            self._live_segment = None

        for index, candidate in enumerate(candidates):
            is_tail = index == len(candidates) - 1
            matches_live = previous_live is not None and index == matched_index

            if matches_live:
                candidate = self._preserve_longer_prefix(previous_live, candidate)
                candidate = replace(candidate, id=previous_live.id)
                if not is_tail or final:
                    candidate = replace(candidate, is_final=True)
                    emitted.append(candidate)
                    self._committed_until_ms = max(
                        self._committed_until_ms, candidate.end_ms
                    )
                    self._live_segment = None
                else:
                    candidate = replace(candidate, is_final=False)
                    emitted.append(candidate)
                    self._live_segment = candidate
                continue

            # A later window repeats the already committed context. Never let
            # those old hypotheses revise confirmed rows, even if their wording
            # or sentence boundaries changed this time.
            if candidate.start_ms < self._committed_until_ms:
                overlaps_committed_tail = (
                    is_tail
                    and candidate.end_ms > self._committed_until_ms
                    and self._committed_until_ms - candidate.start_ms <= 500
                )
                if not overlaps_committed_tail:
                    continue

            if is_tail and not final:
                candidate = replace(candidate, is_final=False)
                self._live_segment = candidate
                emitted.append(candidate)
            else:
                candidate = replace(candidate, is_final=True)
                emitted.append(candidate)
                self._committed_until_ms = max(
                    self._committed_until_ms, candidate.end_ms
                )

        if final:
            self._live_segment = None
        return sorted(emitted, key=lambda item: (item.start_ms, item.end_ms))

    # Matching the live draft is a judgement about speech, not identity: is the
    # newest sentence of this decode the row already on screen, growing, or a
    # different utterance that merely sits next to it? The tolerances lean loose
    # on wording -- a reworded tail must stay updatable instead of freezing and
    # then repeating itself -- and tighter on time: two utterances said back to
    # back rarely overlap for a third of the shorter span within a second of
    # each other, which is what an actual rewrite does. The costs are not
    # symmetric: a wrong match only rewrites a row the reader already treats as
    # provisional (is_final=False), while a miss freezes that row into history
    # and suppresses the rewrite, so the thresholds sit near the loose end.
    @staticmethod
    def _match_live_segment(
        previous: Optional[TranscriptSegment], candidates: List[TranscriptSegment]
    ) -> Optional[int]:
        """Match only the active tail, not arbitrary overlapping history."""
        if previous is None:
            return None
        possible = []
        previous_text = re.sub(r"[^a-z0-9\u3400-\u9fff]", "", previous.text.casefold())
        previous_tokens = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", previous.text.casefold())
        for index, candidate in enumerate(candidates):
            start_delta = abs(candidate.start_ms - previous.start_ms)
            overlap_ms = max(0, min(candidate.end_ms, previous.end_ms) - max(
                candidate.start_ms, previous.start_ms
            ))
            shorter_duration = min(
                previous.end_ms - previous.start_ms,
                candidate.end_ms - candidate.start_ms,
            )
            candidate_text = re.sub(r"[^a-z0-9\u3400-\u9fff]", "", candidate.text.casefold())
            candidate_tokens = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", candidate.text.casefold())
            text_similarity = SequenceMatcher(
                None, previous_text, candidate_text, autojunk=False
            ).ratio()
            is_growing_prefix = (
                len(previous_text) >= 2
                and len(candidate_tokens) >= len(previous_tokens)
                and candidate_tokens[: len(previous_tokens)] == previous_tokens
                and start_delta <= 500
                and shorter_duration > 0
                and overlap_ms / shorter_duration >= 0.7
            )
            if (
                start_delta <= 1000
                and shorter_duration > 0
                and overlap_ms / shorter_duration >= 0.35
                and (text_similarity >= 0.28 or is_growing_prefix)
            ):
                possible.append((start_delta, -candidate.end_ms, index))
        if not possible:
            return None
        return min(possible)[2]

    @staticmethod
    def _preserve_longer_prefix(
        previous: TranscriptSegment, candidate: TranscriptSegment
    ) -> TranscriptSegment:
        """Do not turn a fuller live hypothesis into its shorter prefix."""
        previous_text = re.sub(r"[^a-z0-9\u3400-\u9fff]", "", previous.text.casefold())
        candidate_text = re.sub(r"[^a-z0-9\u3400-\u9fff]", "", candidate.text.casefold())
        if (
            len(candidate_text) < len(previous_text)
            and candidate_text
            and previous_text.startswith(candidate_text)
        ):
            return replace(
                candidate,
                text=previous.text,
                start_ms=min(previous.start_ms, candidate.start_ms),
                end_ms=max(previous.end_ms, candidate.end_ms),
            )
        return candidate

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
