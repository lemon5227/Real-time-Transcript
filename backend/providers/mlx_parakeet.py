"""Apple Silicon MLX provider backed by the Parakeet TDT streaming model."""

from __future__ import annotations

import importlib.util
from copy import copy
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

# The streaming decoder keeps `left` encoder frames of history and refuses to
# finalize the last `right` frames, because those still lack right context.
# One encoder frame is 8 (subsampling) * 160 (hop) / 16000 = 0.08s, so the
# confirmation lag is exactly `right * 0.08` seconds: the shipped default of
# 64 cost 5.12s before any caption could be confirmed.
PARAKEET_LEFT_CONTEXT = 256
PARAKEET_RIGHT_CONTEXT_DEFAULT = 32
PARAKEET_RIGHT_CONTEXT_MIN = 1
PARAKEET_RIGHT_CONTEXT_MAX = 256
ENCODER_FRAME_SECONDS = 0.08
MIN_SILENCE_GAP_SECONDS = 0.45
MIN_WORDS_FOR_FORCED_SPLIT = 20
MAX_UNPUNCTUATED_SEGMENT_SECONDS = 8.0


def mlx_runtime_available() -> bool:
    try:
        return importlib.util.find_spec("parakeet_mlx") is not None
    except (ImportError, ValueError):
        return False


class MlxParakeetProvider:
    name = "mlx"
    requires_contiguous_audio = True
    # A confirmed caption is what both the transcript and the translation queue
    # wait for, so the cap decides how long a run-on sentence can delay its own
    # translation. 24 words could hold a line for 10+ seconds of speech.
    max_words_in_current_draft = 32

    def __init__(
        self,
        model_ref: str,
        loader: Optional[Callable[..., object]] = None,
        right_context: int = PARAKEET_RIGHT_CONTEXT_DEFAULT,
    ):
        self.model = model_ref
        self._loader = loader
        self._right_context = self._clamp_right_context(right_context)
        self._model = None
        self._stream = None
        self._audio_converter = None
        self._config: Optional[SessionConfig] = None
        self._audio_samples = 0
        self._segment_number = 0
        self._emitted: Set[Tuple[int, int, str]] = set()
        self._finalized_token_count = 0
        self._pending_final_tokens: List[object] = []
        self._timeline_cursor_ms = 0
        self._last_window_origin_seconds = 0.0
        self._last_draft = ""

    @classmethod
    def _clamp_right_context(cls, value: object) -> int:
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return PARAKEET_RIGHT_CONTEXT_DEFAULT
        return max(PARAKEET_RIGHT_CONTEXT_MIN, min(PARAKEET_RIGHT_CONTEXT_MAX, number))

    def confirmation_lag_seconds(self) -> float:
        """How long the decoder holds audio back before it can confirm text."""
        return round(self._right_context * ENCODER_FRAME_SECONDS, 3)

    def start(self, config: SessionConfig) -> None:
        self._config = config
        self._audio_samples = 0
        self._segment_number = 0
        self._emitted.clear()
        self._finalized_token_count = 0
        self._pending_final_tokens = []
        self._timeline_cursor_ms = 0
        self._last_window_origin_seconds = 0.0
        self._last_draft = ""
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
                # Keep a generous left context for lecture terminology while
                # reducing the right context so confirmed text arrives in a
                # useful time for live classes. Every right-context frame costs
                # ENCODER_FRAME_SECONDS of confirmation lag (64 frames used to
                # hold captions back by 5.12s).
                context_size=(PARAKEET_LEFT_CONTEXT, self._right_context),
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
        try:
            runtime_audio = self._audio_converter(chunk) if self._audio_converter else chunk
            self._stream.add_audio(runtime_audio)
            self._audio_samples += int(chunk.size)
        except Exception as exc:
            raise ProviderError(
                "MLX_INFERENCE_FAILED",
                "Mac MLX 本地推理失败",
                "降低系统负载、检查 requirements-mac.txt，或切换到云端",
            ) from exc

        if self._uses_token_stability_protocol():
            self._last_window_origin_seconds = self._stream_time_origin_seconds()
            return self._push_stable_stream(chunk)

        result = self._stream.result
        segments = self._new_sentence_segments(result)
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

    def _uses_token_stability_protocol(self) -> bool:
        return self._stream is not None and all(
            hasattr(self._stream, attribute)
            for attribute in ("finalized_tokens", "draft_tokens")
        )

    def _push_stable_stream(self, chunk: np.ndarray) -> List[TranscriptSegment]:
        # result.sentences is a rolling hypothesis whose text and timestamps
        # can be rewritten on every add_audio call. Only finalized_tokens are
        # safe to commit to the historical transcript.
        self._collect_new_finalized_tokens(self._last_window_origin_seconds)
        completed, self._pending_final_tokens = self._split_completed_sentences(
            self._pending_final_tokens
        )
        output = self._segments_from_token_groups(completed)

        draft_tokens = self._absolute_tokens(
            list(getattr(self._stream, "draft_tokens", None) or []),
            self._last_window_origin_seconds,
        )
        draft = self._tokens_text(self._pending_final_tokens + draft_tokens)
        draft = self._bounded_draft_text(draft)
        if draft:
            if draft != self._last_draft:
                self._last_draft = draft
                output.append(
                    TranscriptSegment(
                        id="mlx-draft",
                        text=draft,
                        start_ms=self._draft_start_ms(
                            self._pending_final_tokens + draft_tokens,
                            chunk,
                        ),
                        end_ms=self._draft_end_ms(
                            self._pending_final_tokens + draft_tokens,
                            chunk,
                        ),
                        is_final=False,
                    )
                )
        else:
            self._last_draft = ""
        return output

    def _stream_time_origin_seconds(self) -> float:
        if self._model is None or self._stream is None:
            return 0.0
        preprocessor = getattr(self._model, "preprocessor_config", None)
        sample_rate = float(getattr(preprocessor, "sample_rate", 16000) or 16000)
        hop_length = float(getattr(preprocessor, "hop_length", 160) or 160)
        mel_buffer = getattr(self._stream, "mel_buffer", None)
        shape = getattr(mel_buffer, "shape", None)
        if not shape or len(shape) < 2:
            return max(0.0, self._audio_samples / sample_rate)
        try:
            mel_frames = float(shape[1])
        except (TypeError, ValueError, IndexError):
            return max(0.0, self._audio_samples / sample_rate)
        retained_seconds = max(0.0, mel_frames * hop_length / sample_rate)
        return max(0.0, self._audio_samples / sample_rate - retained_seconds)

    @staticmethod
    def _absolute_token(token: object, origin_seconds: float) -> object:
        adjusted = copy(token)
        start = float(getattr(token, "start", 0.0) or 0.0) + origin_seconds
        end = getattr(token, "end", None)
        if end is None:
            end = start - origin_seconds + float(getattr(token, "duration", 0.0) or 0.0)
        end = float(end or 0.0) + origin_seconds
        adjusted.start = start
        adjusted.end = max(start, end)
        return adjusted

    @classmethod
    def _absolute_tokens(cls, tokens: List[object], origin_seconds: float) -> List[object]:
        return [cls._absolute_token(token, origin_seconds) for token in tokens]

    def _collect_new_finalized_tokens(self, origin_seconds: float) -> None:
        finalized_tokens = list(getattr(self._stream, "finalized_tokens", None) or [])
        if len(finalized_tokens) < self._finalized_token_count:
            # A stream should not move backwards, but resetting here keeps a
            # reused/faulty runtime from duplicating the entire transcript.
            self._finalized_token_count = 0
            self._pending_final_tokens = []
        new_tokens = finalized_tokens[self._finalized_token_count :]
        self._pending_final_tokens.extend(
            self._absolute_tokens(new_tokens, origin_seconds)
        )
        self._finalized_token_count = len(finalized_tokens)

    @staticmethod
    def _token_text(token: object) -> str:
        return str(getattr(token, "text", "") or "")

    @classmethod
    def _tokens_text(cls, tokens: List[object]) -> str:
        return "".join(cls._token_text(token) for token in tokens).strip()

    @classmethod
    def _split_completed_sentences(
        cls, tokens: List[object]
    ) -> Tuple[List[List[object]], List[object]]:
        completed: List[List[object]] = []
        sentence_start = 0
        for index, token in enumerate(tokens):
            token_text = cls._token_text(token).rstrip()
            next_text = cls._token_text(tokens[index + 1]) if index + 1 < len(tokens) else ""
            is_boundary = any(token_text.endswith(mark) for mark in ("!", "?", "。", "？", "！"))
            is_boundary = is_boundary or (
                token_text.endswith(".")
                and (index == len(tokens) - 1 or " " in next_text)
            )
            words_so_far = len(cls._tokens_text(tokens[sentence_start : index + 1]).split())
            has_natural_pause = False
            is_over_duration = False
            if index < len(tokens) - 1:
                next_start = float(getattr(tokens[index + 1], "start", 0.0) or 0.0)
                token_end = getattr(token, "end", None)
                if token_end is None:
                    token_end = float(getattr(token, "start", 0.0) or 0.0) + float(
                        getattr(token, "duration", 0.0) or 0.0
                    )
                token_end = float(token_end or 0.0)
                has_natural_pause = next_start - token_end >= MIN_SILENCE_GAP_SECONDS
                sentence_start_time = float(
                    getattr(tokens[sentence_start], "start", 0.0) or 0.0
                )
                is_over_duration = (
                    token_end - sentence_start_time >= MAX_UNPUNCTUATED_SEGMENT_SECONDS
                    and words_so_far >= MIN_WORDS_FOR_FORCED_SPLIT
                )
            if is_boundary or has_natural_pause or is_over_duration:
                completed.append(tokens[sentence_start : index + 1])
                sentence_start = index + 1
        return completed, tokens[sentence_start:]

    @classmethod
    def _bounded_draft_text(cls, text: str) -> str:
        words = text.split()
        if len(words) <= cls.max_words_in_current_draft:
            return text
        return "… " + " ".join(words[-cls.max_words_in_current_draft :])

    @classmethod
    def _token_group_duration_ms(cls, tokens: List[object]) -> int:
        if not tokens:
            return 1
        starts = [float(getattr(token, "start", 0.0) or 0.0) for token in tokens]
        ends = []
        for token, start in zip(tokens, starts):
            end = getattr(token, "end", None)
            if end is None:
                end = start + float(getattr(token, "duration", 0.0) or 0.0)
            ends.append(float(end or start))
        duration = round(max(0.0, max(ends) - min(starts)) * 1000)
        return max(1, duration or len(tokens) * 120)

    def _segments_from_token_groups(
        self, groups: List[List[object]], *, final: bool = True
    ) -> List[TranscriptSegment]:
        output: List[TranscriptSegment] = []
        for tokens in groups:
            text = self._tokens_text(tokens)
            if self._timeline_cursor_ms > 0 and text.startswith(".") and not text.startswith("..."):
                text = text[1:].lstrip()
            if not text:
                continue
            start_ms, end_ms = self._token_group_span_ms(tokens)
            if start_ms is None or end_ms is None:
                start_ms = self._timeline_cursor_ms
                end_ms = start_ms + self._token_group_duration_ms(tokens)
            else:
                start_ms = max(self._timeline_cursor_ms, start_ms)
                end_ms = max(start_ms + 1, end_ms)
            self._timeline_cursor_ms = max(self._timeline_cursor_ms, end_ms)
            self._segment_number += 1
            output.append(
                TranscriptSegment(
                    id="mlx-%d" % self._segment_number,
                    text=text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    is_final=final,
                )
            )
        return output

    @classmethod
    def _token_group_span_ms(
        cls, tokens: List[object]
    ) -> Tuple[Optional[int], Optional[int]]:
        if not tokens:
            return None, None
        starts = []
        ends = []
        for token in tokens:
            try:
                start = float(getattr(token, "start", 0.0) or 0.0)
                end = getattr(token, "end", None)
                if end is None:
                    end = start + float(getattr(token, "duration", 0.0) or 0.0)
                end = float(end or start)
            except (TypeError, ValueError):
                return None, None
            if not np.isfinite(start) or not np.isfinite(end):
                return None, None
            starts.append(start)
            ends.append(end)
        start_ms = max(0, round(min(starts) * 1000))
        end_ms = max(start_ms + 1, round(max(ends) * 1000))
        return start_ms, end_ms

    def _draft_start_ms(self, tokens: List[object], chunk: np.ndarray) -> int:
        start_ms, _ = self._token_group_span_ms(tokens)
        if start_ms is not None:
            return max(self._timeline_cursor_ms, start_ms)
        return self._timeline_cursor_ms

    def _draft_end_ms(self, tokens: List[object], chunk: np.ndarray) -> int:
        _, end_ms = self._token_group_span_ms(tokens)
        if end_ms is not None:
            return max(self._draft_start_ms(tokens, chunk) + 1, end_ms)
        return max(
            self._timeline_cursor_ms + 1,
            self._timeline_cursor_ms + round(chunk.size * 1000 / 16000),
        )

    def _new_sentence_segments(self, result: object) -> List[TranscriptSegment]:
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
            start_ms = max(0, round(start * 1000))
            end_ms = max(start_ms, round(end * 1000))
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
        if self._uses_token_stability_protocol():
            self._collect_new_finalized_tokens(self._last_window_origin_seconds)
            completed, remaining = self._split_completed_sentences(
                self._pending_final_tokens
            )
            output = self._segments_from_token_groups(completed)
            tail = remaining + self._absolute_tokens(
                list(getattr(self._stream, "draft_tokens", None) or []),
                self._last_window_origin_seconds,
            )
            if self._tokens_text(tail):
                output.extend(self._segments_from_token_groups([tail]))
            self._pending_final_tokens = []
            self._last_draft = ""
            return output
        return self._new_sentence_segments(self._stream.result)

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
