import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Union

import numpy as np

from .audio_pipeline import AudioWindow, AudioWindowBuffer, decode_pcm16_base64
from .glossary import Glossary
from .models import SessionConfig, TranscriptSegment
from .providers.base import ProviderError, TranscriptionProvider
from .segment_merger import SegmentMerger
from .timeline import CaptureTimeline
from .voice_gate import VoiceGate

ProviderFactoryType = Union[Callable[[SessionConfig], TranscriptionProvider], object]
EmitCallback = Callable[[str, str, Dict[str, object]], None]


@dataclass
class _QueuedAudio:
    audio: np.ndarray
    sample_rate: int
    sequence: int


@dataclass
class _SessionState:
    sid: str
    session_id: str
    config: SessionConfig
    provider: TranscriptionProvider
    audio_buffer: Optional[AudioWindowBuffer]
    merger: SegmentMerger
    audio_queue: queue.Queue
    stop_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    worker: Optional[threading.Thread] = None
    startup_event: threading.Event = field(default_factory=threading.Event)
    startup_error: Optional[Exception] = None
    startup_deadline: float = 0.0
    startup_timeout_reported: bool = False
    last_sequence: int = -1
    error: Optional[ProviderError] = None
    timeline: CaptureTimeline = field(default_factory=CaptureTimeline)
    voice_gate: Optional[VoiceGate] = None
    glossary: Glossary = field(default_factory=Glossary)


class SessionManager:
    def __init__(
        self,
        provider_factory: ProviderFactoryType,
        emit: Optional[EmitCallback] = None,
        max_payload_bytes: int = 2_000_000,
        startup_timeout_seconds: float = 45.0,
        streaming_chunk_seconds: float = 1.0,
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
            state = _SessionState(
                sid=sid,
                session_id="session-" + uuid.uuid4().hex,
                startup_deadline=time.monotonic() + self._startup_timeout_seconds,
                config=config,
                provider=provider,
                audio_buffer=None,
                merger=SegmentMerger(),
                audio_queue=queue.Queue(maxsize=config.max_queue),
            )
            state.worker = threading.Thread(
                target=self._run_worker,
                args=(state,),
                name="transcription-%s" % sid[:8],
                daemon=True,
            )
            self._sessions[sid] = state
        state.worker.start()
        return {
            "status": "starting",
            "ready": False,
            "session_id": state.session_id,
            "provider": getattr(provider, "name", "unknown"),
            "model": getattr(provider, "model", None),
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
        try:
            state.audio_queue.put_nowait(_QueuedAudio(audio, 16000, sequence))
            return False
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
                state.timeline.mark_dropped(round(discarded.audio.size * 1000 / 16000))
            state.audio_queue.put_nowait(_QueuedAudio(audio, 16000, sequence))
            return True

    def stop(self, sid: str) -> Dict[str, object]:
        with self._lock:
            state = self._sessions.get(sid)
        if state is None:
            return {"status": "success", "already_stopped": True}

        state.stop_event.set()
        self._enqueue_stop(state)
        if state.worker is not None:
            state.worker.join(timeout=state.config.stop_timeout_seconds)
        # A worker still alive here is one whose model never finished loading.
        # Reporting success would claim the session closed cleanly when it did
        # not, so the caller is told the shutdown is still in progress.
        still_closing = state.worker is not None and state.worker.is_alive()

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
        }
        if state.voice_gate is not None:
            # Makes the quiet-room saving visible instead of silently counted.
            result["silence_skipped_seconds"] = round(state.voice_gate.skipped_seconds, 2)
        if state.error is not None:
            result["error"] = state.error.to_dict()
        if still_closing:
            result["detail"] = "模型仍在加载，稍后会自动释放"
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
        state.error = error
        self._emit(state.sid, "transcription_error", error.to_dict())

    def _make_provider(self, config: SessionConfig) -> TranscriptionProvider:
        factory = self._provider_factory
        if hasattr(factory, "create"):
            return factory.create(config)
        return factory(config)  # type: ignore[operator]

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

    def _run_worker(self, state: _SessionState) -> None:
        try:
            # MLX streams are thread-affine. Keep provider startup, inference,
            # flush, and close on this same worker thread.
            state.provider.start(state.config)
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
                    self._process_window(state, window)

            for window in state.audio_buffer.flush():
                self._process_window(state, window)
            for segment in state.provider.flush():
                self._emit_segments(state, [segment], 0)
        except ProviderError as exc:
            if not state.startup_event.is_set():
                state.startup_error = exc
                state.error = exc
                state.startup_event.set()
                self._emit(state.sid, "transcription_error", exc.to_dict())
            else:
                state.error = exc
                self._emit(state.sid, "transcription_error", exc.to_dict())
        except Exception as exc:
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
        segments = state.provider.push(audio)
        # Stateful providers such as Parakeet already maintain a continuous
        # timeline. Windowed providers return offsets relative to this window.
        window_origin_ms = (
            0 if getattr(state.provider, "requires_contiguous_audio", False) else window.start_ms
        )
        self._emit_segments(state, segments, window_origin_ms)

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
            final_segments.append(
                TranscriptSegment(
                    id=segment.id,
                    text=corrected,
                    start_ms=start_ms,
                    end_ms=state.timeline.absolute_ms(segment.end_ms + window_start_ms),
                    is_final=True,
                    confidence=segment.confidence,
                )
            )
        for segment in state.merger.add(final_segments):
            self._emit(state.sid, "transcript_segment", segment.to_dict())
        for segment in segments:
            if segment.is_final:
                continue
            text = state.glossary.correct(segment.text).strip()
            if not text:
                continue
            candidate = TranscriptSegment(
                id=segment.id,
                text=text,
                start_ms=state.timeline.absolute_ms(segment.start_ms + window_start_ms),
                end_ms=state.timeline.absolute_ms(segment.end_ms + window_start_ms),
                is_final=False,
                confidence=segment.confidence,
            )
            self._emit(state.sid, "transcript_segment", candidate.to_dict())
