from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .glossary import parse_glossary
from .voice_gate import DEFAULT_SILENCE_RMS, normalize_threshold

SUPPORTED_SAMPLE_RATES: Tuple[int, ...] = (8000, 16000, 22050, 32000, 44100, 48000)
SUPPORTED_MODES: Tuple[str, ...] = ("auto", "local", "cloud")


@dataclass(frozen=True)
class SessionConfig:
    mode: str
    model: Optional[str]
    language: str
    sample_rate: int
    enable_vad: bool = True
    window_seconds: float = 3.0
    overlap_seconds: float = 0.5
    max_queue: int = 64
    stop_timeout_seconds: float = 5.0
    silence_rms_threshold: float = DEFAULT_SILENCE_RMS
    glossary: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in SUPPORTED_MODES:
            raise ValueError("mode must be auto, local or cloud")
        if not self.language or len(self.language) > 16:
            raise ValueError("language must be a non-empty short code")
        if self.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError("sample_rate is not supported")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.overlap_seconds < 0 or self.overlap_seconds >= self.window_seconds:
            raise ValueError("overlap_seconds must be between zero and window_seconds")
        if self.max_queue < 1:
            raise ValueError("max_queue must be positive")
        if self.stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be positive")
        object.__setattr__(
            self, "silence_rms_threshold", normalize_threshold(self.silence_rms_threshold)
        )
        object.__setattr__(self, "glossary", parse_glossary(self.glossary))


@dataclass(frozen=True)
class TranscriptSegment:
    id: str
    text: str
    start_ms: int
    end_ms: int
    is_final: bool
    confidence: Optional[float] = None
    speaker_id: Optional[str] = None
    speaker_confidence: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("segment id must not be empty")
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("segment timestamps are invalid")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        if self.speaker_id is not None and not self.speaker_id:
            raise ValueError("speaker_id must not be empty")
        if self.speaker_confidence is not None and not 0 <= self.speaker_confidence <= 1:
            raise ValueError("speaker confidence must be between zero and one")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "is_final": self.is_final,
            "confidence": self.confidence,
        }
        if self.speaker_id is not None:
            payload["speaker_id"] = self.speaker_id
        if self.speaker_confidence is not None:
            payload["speaker_confidence"] = self.speaker_confidence
        return payload
