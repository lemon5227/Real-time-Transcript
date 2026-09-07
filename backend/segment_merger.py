import re
from typing import Iterable, List

from .models import TranscriptSegment


def _normalized_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]", "", compact)


def _overlap_ratio(left: TranscriptSegment, right: TranscriptSegment) -> float:
    intersection = max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    shortest = max(1, min(left.end_ms - left.start_ms, right.end_ms - right.start_ms))
    return intersection / shortest


class SegmentMerger:
    def __init__(self) -> None:
        self._segments: List[TranscriptSegment] = []

    def add(
        self, segments: Iterable[TranscriptSegment], window_start_ms: int
    ) -> List[TranscriptSegment]:
        accepted: List[TranscriptSegment] = []
        for segment in segments:
            candidate = TranscriptSegment(
                id=segment.id,
                text=segment.text.strip(),
                start_ms=segment.start_ms + window_start_ms,
                end_ms=segment.end_ms + window_start_ms,
                is_final=segment.is_final,
                confidence=segment.confidence,
            )
            if not candidate.text:
                continue
            duplicate = any(
                _normalized_text(existing.text) == _normalized_text(candidate.text)
                and _overlap_ratio(existing, candidate) >= 0.3
                for existing in self._segments
            )
            if duplicate:
                continue
            self._segments.append(candidate)
            accepted.append(candidate)

        self._segments.sort(key=lambda item: (item.start_ms, item.end_ms, item.id))
        return sorted(accepted, key=lambda item: (item.start_ms, item.end_ms, item.id))

    def all_segments(self) -> List[TranscriptSegment]:
        return list(self._segments)
