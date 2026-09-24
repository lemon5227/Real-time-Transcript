import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Union

import numpy as np

from .audio_pipeline import AudioWindow, AudioWindowBuffer, decode_pcm16_base64
from .diarization import SpeakerTimeline
from .glossary import Glossary
from .logging_setup import get_logger
from .models import SessionConfig, TranscriptSegment
from .providers.base import ProviderError, TranscriptionProvider
from .providers.diarization import NullSpeakerDiarizer, SpeakerDiarizer
from .segment_merger import SegmentMerger
from .timeline import CaptureTimeline
from .voice_gate import VoiceGate

logger = get_logger("session")

ProviderFactoryType = Union[Callable[[SessionConfig], TranscriptionProvider], object]
DiarizerFactoryType = Callable[[SessionConfig], SpeakerDiarizer]
EmitCallback = Callable[[str, str, Dict[str, object]], None]

# A session that spends longer decoding than the audio it receives is losing
# ground; the live caption is about to drift behind the lecturer. Measured over
# the whole session rather than per chunk: a windowed provider decodes an 18 s
# window when a 1 s chunk completes a hop, so dividing one decode by the chunk
# that triggered it says "0.74x" for a path that is really running 25x realtime.
SLOW_DECODE_REALTIME_RATIO = 0.5

# Falling behind is a condition, not an event, so report it on a timer instead
# of once per chunk -- a genuinely slow path otherwise writes a line every
# second and buries everything else in the log.
SLOW_DECODE_LOG_INTERVAL_SECONDS = 10.0


class AdaptiveStreamingChunkPolicy:
    """Increase delivery chunks only when the inference worker falls behind."""

    def __init__(self, base_seconds: float, max_seconds: float = 1.5):
        if base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        self._base_seconds = base_seconds
        self._max_seconds = max(base_seconds, max_seconds)
        self.current_seconds = base_seconds

    def observe(self, inference_seconds: float, queued_chunks: int) -> float:
        overloaded = queued_chunks >= 4 or inference_seconds > self.current_seconds * 0.9
        has_headroom = queued_chunks == 0 and inference_seconds < self.current_seconds * 0.55
        if overloaded and self.current_seconds < self._max_seconds:
            self.current_seconds = min(self._max_seconds, self.current_seconds + 0.25)
        elif has_headroom and self.current_seconds > self._base_seconds:
            self.current_seconds = max(self._base_seconds, self.current_seconds - 0.25)
        self.current_seconds = round(self.current_seconds, 2)
        return self.current_seconds


@dataclass
class _QueuedAudio:
    audio: np.ndarray
    sample_rate: int
    sequence: int
    recording_start_ms: int = 0


@dataclass
class _SessionState:
    sid: str
    session_id: str
    config: SessionConfig
    provider: TranscriptionProvider
    diarizer: SpeakerDiarizer
    audio_buffer: Optional[AudioWindowBuffer]
    merger: SegmentMerger
    audio_queue: queue.Queue
    diarization_queue: queue.Queue
    stop_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    worker: Optional[threading.Thread] = None
    diarization_worker: Optional[threading.Thread] = None
    startup_event: threading.Event = field(default_factory=threading.Event)
    startup_error: Optional[Exception] = None
    startup_deadline: float = 0.0
    startup_timeout_reported: bool = False
    last_sequence: int = -1
    error: Optional[ProviderError] = None
    timeline: CaptureTimeline = field(default_factory=CaptureTimeline)
    voice_gate: Optional[VoiceGate] = None
    glossary: Glossary = field(default_factory=Glossary)
    decode_seconds_total: float = 0.0
    audio_seconds_total: float = 0.0
    slow_decode_logged_at: Optional[float] = None
    emitted_segment_ids: Set[str] = field(default_factory=set)
    removed_segment_count: int = 0
    speaker_timeline: SpeakerTimeline = field(default_factory=SpeakerTimeline)
    capture_audio_ms: int = 0


class SessionManager:
    def __init__(
        self,
        provider_factory: ProviderFactoryType,
        emit: Optional[EmitCallback] = None,
        max_payload_bytes: int = 2_000_000,
        startup_timeout_seconds: float = 45.0,
        streaming_chunk_seconds: float = 1.0,
        diarizer_factory: Optional[DiarizerFactoryType] = None,
    ):
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if streaming_chunk_seconds <= 0:
            raise ValueError("streaming_chunk_seconds must be positive")
        self._provider_factory = provider_factory
        self._emit = emit or (lambda _sid, _event, _payload: None)
        self._max_payload_bytes = max_payload_bytes
        self._startup_timeout_seconds = startup_timeout_seconds
        self._streaming_chunk_seconds = streaming_chunk_seconds
        self._diarizer_factory = diarizer_factory or (lambda _config: NullSpeakerDiarizer())
        self._sessions: Dict[str, _SessionState] = {}
        # Sessions that were asked to stop but whose worker is still winding
        # down, usually because the model was still loading. Kept so a fast
        # stop-then-start does not load a second copy of the model alongside the
        # first one.
        self._closing: Dict[str, _SessionState] = {}
        self._lock = threading.RLock()

    def start(self, sid: str, config: SessionConfig) -> Dict[str, object]:
        with self._lock:
            if sid in self._sessions:
                raise ValueError("SESSION_ALREADY_ACTIVE: this connection already has a session")
            pending = self._closing.pop(sid, None)

        if pending is not None and pending.worker is not None and pending.worker.is_alive():
            # The previous session on this connection is still loading its model.
            # Give it a moment rather than loading a second copy beside it.
            pending.worker.join(timeout=config.stop_timeout_seconds)

        with self._lock:
            if sid in self._sessions:
                raise ValueError("SESSION_ALREADY_ACTIVE: this connection already has a session")
            provider = self._make_provider(config)
            diarizer = self._make_diarizer(config)
            state = _SessionState(
                sid=sid,
                session_id="session-" + uuid.uuid4().hex,
                startup_deadline=time.monotonic() + self._startup_timeout_seconds,
                config=config,
                provider=provider,
                diarizer=diarizer,
                audio_buffer=None,
                merger=SegmentMerger(),
                audio_queue=queue.Queue(maxsize=config.max_queue),
                diarization_queue=queue.Queue(maxsize=config.diarization_queue),
            )
            state.worker = threading.Thread(
                target=self._run_worker,
                args=(state,),
                name="transcription-%s" % sid[:8],
                daemon=True,
            )
            self._sessions[sid] = state
        state.worker.start()
        if config.enable_diarization:
            state.diarization_worker = threading.Thread(
                target=self._run_diarization_worker,
                args=(state,),
                name="diarization-%s" % sid[:8],
                daemon=True,
            )
            state.diarization_worker.start()
        logger.info(
            "会话开始 sid=%s session=%s mode=%s model=%s language=%s "
            "sample_rate=%s vad=%s window=%.2fs overlap=%.2fs",
            sid,
            state.session_id,
            config.mode,
            getattr(provider, "model", None) or config.model,
            config.language,
            config.sample_rate,
            config.enable_vad,
            config.window_seconds,
            config.overlap_seconds,
        )
        return {
            "status": "starting",
            "ready": False,
            "session_id": state.session_id,
            "provider": getattr(provider, "name", "unknown"),
            "model": getattr(provider, "model", None),
            "diarization": {
                "enabled": config.enable_diarization,
                "variant": config.diarization_variant,
                "provider": getattr(diarizer, "name", "disabled"),
            },
        }

    def push_audio(
        self,
        sid: str,
        encoded_audio: str,
        sample_rate: int,
        sequence: int,
        offset_ms: Optional[int] = None,
    ) -> bool:
        with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                raise ValueError("SESSION_NOT_ACTIVE: no active transcription session")
            if sequence <= state.last_sequence:
                raise ValueError("INVALID_AUDIO_SEQUENCE: sequence must increase")
            state.last_sequence = sequence
            state.timeline.observe(offset_ms)

        self._check_startup_timeout(state)
        audio = decode_pcm16_base64(
            encoded_audio,
            sample_rate=sample_rate,
            max_bytes=self._max_payload_bytes,
        )
        if audio.size == 0:
            return False
        with self._lock:
            # The browser offset is the recording clock. It is more reliable
            # than the ASR queue position when a model starts late or a queue
            # drops old work under pressure.
            recording_start_ms = (
                max(0, int(offset_ms))
                if offset_ms is not None
                else state.timeline.origin_ms + state.capture_audio_ms
            )
            state.capture_audio_ms += round(audio.size * 1000 / 16000)
        queued_audio = _QueuedAudio(audio, 16000, sequence, recording_start_ms)
        dropped = False
        try:
            state.audio_queue.put_nowait(queued_audio)
        except queue.Full:
            # Keep the newest audio close to real time when inference is slower
            # than capture. Dropping the oldest pending window is recoverable;
            # turning it into a session-fatal error is not. The dropped audio is
            # still part of the recording, so the timeline keeps its duration.
            try:
                discarded = state.audio_queue.get_nowait()
            except queue.Empty:
                discarded = None
            if isinstance(discarded, _QueuedAudio):
                dropped_ms = round(discarded.audio.size * 1000 / 16000)
                state.timeline.mark_dropped(dropped_ms)
                logger.warning(
                    "推理落后于录音，丢弃音频 sid=%s 丢弃=%.2fs 队列=%d",
                    sid,
                    dropped_ms / 1000,
                    state.audio_queue.qsize(),
                )
            state.audio_queue.put_nowait(queued_audio)
            dropped = True

        if state.config.enable_diarization:
            try:
                state.diarization_queue.put_nowait(queued_audio)
            except queue.Full:
                try:
                    state.diarization_queue.get_nowait()
                except queue.Empty:
                    pass
                state.diarization_queue.put_nowait(queued_audio)
                dropped = True
                logger.warning(
                    "说话人识别落后于录音，跳过旧音频 sid=%s 队列=%d",
                    sid,
                    state.diarization_queue.qsize(),
                )
        return dropped

    def stop(self, sid: str) -> Dict[str, object]:
        with self._lock:
            state = self._sessions.get(sid)
        if state is None:
            return {"status": "success", "already_stopped": True}

        state.stop_event.set()
        self._enqueue_stop(state)
        self._enqueue_diarization_stop(state)
        if state.worker is not None:
            state.worker.join(timeout=state.config.stop_timeout_seconds)
        if state.diarization_worker is not None:
            state.diarization_worker.join(timeout=state.config.stop_timeout_seconds)
        # A worker still alive here is one whose model never finished loading.
        # Reporting success would claim the session closed cleanly when it did
        # not, so the caller is told the shutdown is still in progress.
        still_closing = (
            state.worker is not None and state.worker.is_alive()
        ) or (
            state.diarization_worker is not None and state.diarization_worker.is_alive()
        )

        with self._lock:
            self._sessions.pop(sid, None)
            if still_closing:
                self._closing[sid] = state

        result: Dict[str, object] = {
            "status": (
                "stopping" if still_closing else "success" if state.error is None else "error"
            ),
            "session_id": state.session_id,
            "segments": [segment.to_dict() for segment in state.merger.all_segments()],
            "diarization": {
                "enabled": state.config.enable_diarization,
                "provider": getattr(state.diarizer, "name", "disabled"),
            },
        }
        if state.voice_gate is not None:
            # Makes the quiet-room saving visible instead of silently counted.
            result["silence_skipped_seconds"] = round(state.voice_gate.skipped_seconds, 2)
        if state.error is not None:
            result["error"] = state.error.to_dict()
        if still_closing:
            result["detail"] = "模型仍在加载，稍后会自动释放"
        segments = result["segments"]
        if state.audio_seconds_total > 0:
            result["decode_realtime_ratio"] = round(
                state.decode_seconds_total / state.audio_seconds_total, 3
            )
        if state.removed_segment_count:
            # Non-zero means the merger had to retract a caption it had already
            # published. Expected occasionally; a large number means the window
            # is producing unstable sentence boundaries.
            result["removed_segments"] = state.removed_segment_count
        logger.info(
            "会话结束 sid=%s session=%s 状态=%s 确认字幕=%d 段 撤回=%d 静音跳过=%.2fs 丢弃=%.2fs 实时倍率=%.2fx",
            sid,
            state.session_id,
            result["status"],
            len(segments) if isinstance(segments, list) else 0,
            state.removed_segment_count,
            getattr(state.voice_gate, "skipped_seconds", 0.0),
            state.timeline.dropped_ms / 1000,
            result.get("decode_realtime_ratio", 0.0),
        )
        return result

    def cleanup(self, sid: str) -> None:
        self.stop(sid)

    def translation_segments(self, sid: str, segment_ids: List[str]) -> List[Dict[str, object]]:
        with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                raise ValueError("SESSION_NOT_ACTIVE: no active transcription session")
            by_id = {segment.id: segment for segment in state.merger.all_segments()}
        if not segment_ids or any(segment_id not in by_id for segment_id in segment_ids):
            raise ValueError("TRANSLATION_SEGMENT_NOT_FOUND: segment is not part of this session")
        result = []
        for segment_id in segment_ids:
            segment = by_id[segment_id]
            if not segment.is_final:
                raise ValueError("TRANSLATION_SEGMENT_NOT_FINAL: only final segments can be translated")
            result.append({"id": segment.id, "text": segment.text})
        return result

    def _check_startup_timeout(self, state: _SessionState) -> None:
        """Fail a session whose model never becomes ready.

        The worker blocks inside provider startup, so it cannot time itself out.
        Audio keeps arriving while the model loads, which makes every chunk a
        heartbeat this can measure against. Without it the UI waits forever on a
        model that will not load and buffers audio the whole time.
        """
        if state.startup_event.is_set() or state.startup_timeout_reported:
            return
        if time.monotonic() < state.startup_deadline:
            return
        state.startup_timeout_reported = True
        error = ProviderError(
            "PROVIDER_START_TIMEOUT",
            "模型启动超时，仍未开始转录",
            "降低模型大小、检查本地依赖，或切换到云端模式",
        )
        logger.error(
            "模型启动超时 sid=%s 超时=%.1fs",
            state.sid,
            self._startup_timeout_seconds,
        )
        state.error = error
        self._emit(state.sid, "transcription_error", error.to_dict())

    def _make_provider(self, config: SessionConfig) -> TranscriptionProvider:
        factory = self._provider_factory
        if hasattr(factory, "create"):
            return factory.create(config)
        return factory(config)  # type: ignore[operator]

    def _make_diarizer(self, config: SessionConfig) -> SpeakerDiarizer:
        return self._diarizer_factory(config)

    @staticmethod
    def _enqueue_stop(state: _SessionState) -> None:
        try:
            state.audio_queue.put_nowait(None)
        except queue.Full:
            try:
                state.audio_queue.get_nowait()
            except queue.Empty:
                pass
            state.audio_queue.put_nowait(None)

    @staticmethod
    def _enqueue_diarization_stop(state: _SessionState) -> None:
        if state.diarization_worker is None:
            return
        try:
            state.diarization_queue.put_nowait(None)
        except queue.Full:
            try:
                state.diarization_queue.get_nowait()
            except queue.Empty:
                pass
            state.diarization_queue.put_nowait(None)

    def _run_diarization_worker(self, state: _SessionState) -> None:
        try:
            state.diarizer.start(16000, state.config.diarization_variant)
            self._emit(
                state.sid,
                "diarization_status",
                {
                    "status": "ready",
                    "provider": getattr(state.diarizer, "name", "unknown"),
                    "variant": state.config.diarization_variant,
                },
            )
            while True:
                try:
                    item = state.diarization_queue.get(timeout=0.2)
                except queue.Empty:
                    if state.stop_event.is_set():
                        break
                    continue
                if item is None:
                    break
                turns = state.diarizer.push(item.audio, item.recording_start_ms)
                if turns:
                    state.speaker_timeline.add_turns(turns)
                    self._emit_speaker_updates(state)
            turns = state.diarizer.flush()
            if turns:
                state.speaker_timeline.add_turns(turns)
                self._emit_speaker_updates(state)
        except ProviderError as exc:
            logger.warning(
                "说话人识别不可用 sid=%s code=%s message=%s",
                state.sid,
                exc.code,
                exc.message,
            )
            self._emit(
                state.sid,
                "diarization_status",
                {
                    "status": "unavailable",
                    "provider": getattr(state.diarizer, "name", "unknown"),
                    "code": exc.code,
                    "message": exc.message,
                },
            )
        except Exception as exc:
            logger.exception("说话人识别工作线程异常 sid=%s", state.sid)
            self._emit(
                state.sid,
                "diarization_status",
                {
                    "status": "unavailable",
                    "provider": getattr(state.diarizer, "name", "unknown"),
                    "code": "DIARIZATION_WORKER_FAILED",
                    "message": str(exc),
                },
            )
        finally:
            try:
                state.diarizer.close()
            except Exception:
                logger.exception("关闭说话人识别组件失败 sid=%s", state.sid)

    def _emit_speaker_updates(self, state: _SessionState) -> None:
        updates = state.speaker_timeline.updates_for_segments(state.merger.all_segments())
        for segment in updates:
            if not state.merger.replace_segment(segment):
                continue
            self._emit(state.sid, "transcript_segment_updated", segment.to_dict())

    def _run_worker(self, state: _SessionState) -> None:
        try:
            # MLX streams are thread-affine. Keep provider startup, inference,
            # flush, and close on this same worker thread.
            startup_started = time.monotonic()
            state.provider.start(state.config)
            logger.info(
                "provider 就绪 sid=%s provider=%s model=%s 耗时=%.2fs",
                state.sid,
                getattr(state.provider, "name", "unknown"),
                getattr(state.provider, "model", None),
                time.monotonic() - startup_started,
            )
            if state.stop_event.is_set():
                # Stopped while the model was still loading. Announcing a ready
                # session now would revive a session the client already ended.
                state.startup_event.set()
                return
            contiguous = bool(
                getattr(state.provider, "requires_contiguous_audio", False)
            )
            state.audio_buffer = AudioWindowBuffer(
                # A streaming provider keeps its own internal timeline, so the
                # window is only a delivery granularity for it. Feeding it in
                # small chunks keeps the live draft scrolling instead of
                # jumping once every window_seconds. Windowed providers (cloud,
                # Whisper) really do infer once per window, so they keep the
                # configured window size.
                window_seconds=(
                    self._streaming_chunk_seconds
                    if contiguous
                    else state.config.window_seconds
                ),
                overlap_seconds=0.0 if contiguous else state.config.overlap_seconds,
            )
            streaming_policy = (
                AdaptiveStreamingChunkPolicy(self._streaming_chunk_seconds)
                if contiguous
                else None
            )
            state.voice_gate = VoiceGate(
                enabled=state.config.enable_vad,
                threshold=state.config.silence_rms_threshold,
            )
            state.glossary = Glossary(state.config.glossary)
            state.startup_event.set()
            self._emit(
                state.sid,
                "transcription_ready",
                {
                    "status": "ready",
                    "ready": True,
                    "session_id": state.session_id,
                    "provider": getattr(state.provider, "name", "unknown"),
                    "model": getattr(state.provider, "model", None),
                },
            )
            while True:
                try:
                    item = state.audio_queue.get(timeout=0.2)
                except queue.Empty:
                    if state.stop_event.is_set():
                        break
                    continue
                if item is None:
                    break
                queued = item
                for window in state.audio_buffer.append(queued.audio, queued.sample_rate):
                    inference_started = time.monotonic()
                    self._process_window(state, window)
                    if streaming_policy is not None:
                        next_seconds = streaming_policy.observe(
                            time.monotonic() - inference_started,
                            state.audio_queue.qsize(),
                        )
                        if next_seconds != state.audio_buffer.window_seconds:
                            state.audio_buffer.set_window_seconds(next_seconds)

            for window in state.audio_buffer.flush():
                self._process_window(state, window)
            for segment in state.provider.flush():
                self._emit_segments(state, [segment], 0)
        except ProviderError as exc:
            # The message shown in the UI is deliberately generic. The chained
            # cause is the part that makes a classroom failure diagnosable
            # afterwards, so it goes to the log with a traceback.
            logger.error(
                "provider 错误 sid=%s code=%s message=%s 底层原因=%s",
                state.sid,
                exc.code,
                exc.message,
                repr(exc.__cause__) if exc.__cause__ is not None else "无",
                exc_info=exc.__cause__ is not None,
            )
            if not state.startup_event.is_set():
                state.startup_error = exc
                state.error = exc
                state.startup_event.set()
                self._emit(state.sid, "transcription_error", exc.to_dict())
            else:
                state.error = exc
                self._emit(state.sid, "transcription_error", exc.to_dict())
        except Exception as exc:
            logger.exception("转录工作线程异常 sid=%s", state.sid)
            if not state.startup_event.is_set():
                state.startup_error = ProviderError(
                    "PROVIDER_START_FAILED",
                    "实时转录 provider 启动失败",
                    "检查模型依赖或切换到云端",
                )
                state.error = state.startup_error
                state.startup_event.set()
                self._emit(state.sid, "transcription_error", {
                    "code": state.error.code,
                    "message": state.error.message,
                    "action": state.error.action,
                    "detail": str(exc),
                })
                return
            state.error = ProviderError(
                "SESSION_WORKER_FAILED",
                "实时转录工作线程失败",
                "请重新开始会话或切换 provider",
            )
            self._emit(state.sid, "transcription_error", {
                "code": state.error.code,
                "message": state.error.message,
                "action": state.error.action,
                "detail": str(exc),
            })
        finally:
            try:
                state.provider.close()
            finally:
                with self._lock:
                    self._closing.pop(state.sid, None)
                state.finished_event.set()

    def _process_window(self, state: _SessionState, window: AudioWindow) -> None:
        audio = window.audio
        silent = False
        if state.voice_gate is not None:
            audio, silent = state.voice_gate.filter(audio)
        if silent and not getattr(state.provider, "requires_contiguous_audio", False):
            # A windowed provider can skip the window entirely. Its caption
            # positions come from the window offset rather than from the audio it
            # received, so skipping costs nothing and saves the request or the
            # inference. A streaming provider must still see the zeros below.
            return
        inference_started = time.monotonic()
        segments = state.provider.push(audio)
        decode_seconds = time.monotonic() - inference_started
        audio_seconds = audio.size / 16000
        self._record_decode_pace(state, decode_seconds, audio_seconds)
        # Stateful providers such as Parakeet already maintain a continuous
        # timeline. Windowed providers return offsets relative to this window.
        window_origin_ms = (
            0 if getattr(state.provider, "requires_contiguous_audio", False) else window.start_ms
        )
        self._emit_segments(state, segments, window_origin_ms)

    def _record_decode_pace(
        self, state: _SessionState, decode_seconds: float, audio_seconds: float
    ) -> None:
        """Track how much decoding the session is doing per second of audio.

        Naming the ratio makes "the live caption is drifting" measurable instead
        of something you only notice by watching the page.
        """
        if audio_seconds <= 0:
            return
        state.decode_seconds_total += decode_seconds
        state.audio_seconds_total += audio_seconds
        ratio = state.decode_seconds_total / state.audio_seconds_total
        if ratio < SLOW_DECODE_REALTIME_RATIO:
            return
        now = time.monotonic()
        if (
            state.slow_decode_logged_at is not None
            and now - state.slow_decode_logged_at < SLOW_DECODE_LOG_INTERVAL_SECONDS
        ):
            return
        state.slow_decode_logged_at = now
        logger.warning(
            "解码偏慢 sid=%s 累计音频=%.1fs 累计解码=%.1fs 实时倍率=%.2fx 队列=%d",
            state.sid,
            state.audio_seconds_total,
            state.decode_seconds_total,
            ratio,
            state.audio_queue.qsize(),
        )

    def _emit_segments(
        self,
        state: _SessionState,
        segments: List[TranscriptSegment],
        window_start_ms: int,
    ) -> None:
        final_segments = []
        for segment in segments:
            # Providers report offsets relative to the audio they received. The
            # recording timeline additionally carries the pre-model capture
            # origin and any audio dropped by backpressure.
            start_ms = state.timeline.absolute_ms(segment.start_ms + window_start_ms)
            corrected = state.glossary.correct(segment.text)
            if not segment.is_final:
                continue
            candidate = TranscriptSegment(
                id=segment.id,
                text=corrected,
                start_ms=start_ms,
                end_ms=state.timeline.absolute_ms(segment.end_ms + window_start_ms),
                is_final=True,
                confidence=segment.confidence,
            )
            attributed = state.speaker_timeline.updates_for_segments([candidate])
            final_segments.append(attributed[0] if attributed else candidate)
        outcome = state.merger.add(final_segments)
        for segment_id in outcome.removed_ids:
            # The merger compacted two stored captions into one, so this id no
            # longer exists on the server. Telling the client is not optional:
            # without it the duplicate stays on screen for the rest of the
            # lecture, which is the defect this event was added to close.
            state.emitted_segment_ids.discard(segment_id)
            state.removed_segment_count += 1
            logger.info("撤回字幕 sid=%s id=%s", state.sid, segment_id)
            self._emit(state.sid, "transcript_segment_removed", {"id": segment_id})
        for segment in outcome.segments:
            # The confirmed caption is the artefact a bad live transcript gets
            # judged on, so it is logged verbatim rather than summarised. The id
            # and the new/revised marker are here because the merger reuses the
            # id when it replaces an earlier decode: without them the log cannot
            # tell "the caption was corrected in place" from "the feed is full
            # of near-duplicates", which is the first thing worth knowing.
            revised = segment.id in state.emitted_segment_ids
            state.emitted_segment_ids.add(segment.id)
            logger.info(
                "确认字幕 sid=%s id=%s %s %d-%dms %s",
                state.sid,
                segment.id,
                "更新" if revised else "新增",
                segment.start_ms,
                segment.end_ms,
                segment.text,
            )
            self._emit(state.sid, "transcript_segment", segment.to_dict())
        for segment in segments:
            if segment.is_final:
                continue
            text = state.glossary.correct(segment.text).strip()
            if not text:
                continue
            logger.debug("草稿字幕 sid=%s %s", state.sid, text)
            candidate = TranscriptSegment(
                id=segment.id,
                text=text,
                start_ms=state.timeline.absolute_ms(segment.start_ms + window_start_ms),
                end_ms=state.timeline.absolute_ms(segment.end_ms + window_start_ms),
                is_final=False,
                confidence=segment.confidence,
            )
            self._emit(state.sid, "transcript_segment", candidate.to_dict())
