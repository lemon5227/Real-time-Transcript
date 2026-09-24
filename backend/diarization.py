"""Speaker-turn contracts and timestamp-only attribution.

The CoreML helper is intentionally not imported here. Keeping this module pure
lets the live session and its tests use the same attribution rules on every
platform, including machines that only have an ASR provider.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Iterable, List, Optional, Tuple

from .models import TranscriptSegment


@dataclass(frozen=True)
class SpeakerTurn:
    speaker_id: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.speaker_id:
            raise ValueError("speaker_id must not be empty")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("speaker timestamps are invalid")
        if not 0 <= self.confidence <= 1:
            raise ValueError("speaker confidence must be between zero and one")

    def to_dict(self) -> dict:
        return {
            "speaker_id": self.speaker_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "confidence": self.confidence,
        }


class SpeakerTimeline:
    """Keep speaker turns and enrich transcript segments when evidence arrives."""

    MIN_COVERAGE = 0.5
    MIN_MARGIN = 0.1

    def __init__(self) -> None:
        self._turns: List[SpeakerTurn] = []
        self._attributions: dict[str, Tuple[str, float]] = {}
        self._lock = threading.RLock()

    @property
    def turns(self) -> List[SpeakerTurn]:
        with self._lock:
            return list(self._turns)

    def add_turns(self, turns: Iterable[SpeakerTurn]) -> None:
        with self._lock:
            all_turns = self._turns + list(turns)
            self._turns = self._compact_same_speaker_turns(all_turns)

    def attribute(self, segment: TranscriptSegment) -> Optional[Tuple[str, float]]:
        duration = max(0, segment.end_ms - segment.start_ms)
        if duration <= 0:
            return None
        with self._lock:
            overlap_by_speaker: dict[str, int] = {}
            weighted_confidence: dict[str, float] = {}
            for turn in self._turns:
                overlap = max(
                    0,
                    min(segment.end_ms, turn.end_ms)
                    - max(segment.start_ms, turn.start_ms),
                )
                if overlap <= 0:
                    continue
                overlap_by_speaker[turn.speaker_id] = (
                    overlap_by_speaker.get(turn.speaker_id, 0) + overlap
                )
                weighted_confidence[turn.speaker_id] = (
                    weighted_confidence.get(turn.speaker_id, 0.0)
                    + overlap * turn.confidence
                )

            if not overlap_by_speaker:
                return None
            ranked = sorted(
                overlap_by_speaker.items(),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )
            winner, winner_overlap = ranked[0]
            winner_ratio = winner_overlap / duration
            runner_ratio = ranked[1][1] / duration if len(ranked) > 1 else 0.0
            if winner_ratio < self.MIN_COVERAGE or winner_ratio - runner_ratio < self.MIN_MARGIN:
                return None
            confidence = weighted_confidence[winner] / winner_overlap
            return winner, round(confidence, 4)

    def updates_for_segments(
        self, segments: Iterable[TranscriptSegment]
    ) -> List[TranscriptSegment]:
        updates: List[TranscriptSegment] = []
        with self._lock:
            for segment in segments:
                attribution = self.attribute(segment)
                if attribution is None:
                    continue
                speaker_id, confidence = attribution
                previous = self._attributions.get(segment.id)
                current = (speaker_id, confidence)
                if previous is not None and previous == current:
                    continue
                self._attributions[segment.id] = current
                updates.append(
                    replace(
                        segment,
                        speaker_id=speaker_id,
                        speaker_confidence=confidence,
                    )
                )
        return updates

    @staticmethod
    def _compact_same_speaker_turns(turns: Iterable[SpeakerTurn]) -> List[SpeakerTurn]:
        ordered = sorted(turns, key=lambda item: (item.start_ms, item.end_ms, item.speaker_id))
        compacted: List[SpeakerTurn] = []
        for turn in ordered:
            if compacted and compacted[-1].speaker_id == turn.speaker_id and turn.start_ms <= compacted[-1].end_ms:
                previous = compacted.pop()
                total = max(previous.end_ms - previous.start_ms, 1) + max(turn.end_ms - turn.start_ms, 1)
                confidence = (
                    (previous.confidence * (previous.end_ms - previous.start_ms))
                    + (turn.confidence * (turn.end_ms - turn.start_ms))
                ) / total
                compacted.append(
                    SpeakerTurn(
                        speaker_id=turn.speaker_id,
                        start_ms=min(previous.start_ms, turn.start_ms),
                        end_ms=max(previous.end_ms, turn.end_ms),
                        confidence=confidence,
                    )
                )
            else:
                compacted.append(turn)
        return compacted
