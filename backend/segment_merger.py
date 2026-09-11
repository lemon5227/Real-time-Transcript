"""Merge streamed transcript segments and suppress sliding-window duplicates.

Windowed providers see the same speech twice: consecutive windows overlap by
``AUDIO_OVERLAP_SECONDS`` so a sentence is not cut at the window edge. The
overlap makes the model decode part of the audio twice, and the duplicate has to
be removed here because the provider has no memory of the previous window.

Two properties of the duplicate drive the rules below:

* it always comes from a *neighbouring* window, so only the newest segments can
  match, and
* its text is either identical to the one already emitted or strictly contains
  it, because the second window decoded a longer span of the same speech.

Timestamps are absolute from here on. Callers place each segment on the
recording timeline before handing it over, so the merger never shifts them.
"""

import re
from dataclasses import replace
from typing import Iterable, List, Optional, Tuple

from .models import TranscriptSegment

# A duplicate can only come from a neighbouring window, so comparing against the
# tail is enough and keeps a long lecture linear instead of quadratic.
COMPARISON_WINDOW = 8

# A real sliding-window overlap lasts several hundred milliseconds. Anything
# shorter would risk merging a lecturer who repeats a short phrase.
MIN_DUPLICATE_OVERLAP_MS = 100

# Containment alone is too loose: a lecturer who says "Okay" and then "Okay so
# let's begin" also produces one text inside the other. Two decodes of the same
# window do have almost the same duration, which a short phrase followed by a
# longer sentence does not, so similar lengths are what makes containment safe.
CONTAINMENT_DURATION_RATIO = 0.5


def _normalized_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]", "", compact)


def _overlap_ms(left: TranscriptSegment, right: TranscriptSegment) -> int:
    return max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))


def _duration_ms(segment: TranscriptSegment) -> int:
    return max(0, segment.end_ms - segment.start_ms)


def _similar_duration(left: TranscriptSegment, right: TranscriptSegment) -> bool:
    shorter = min(_duration_ms(left), _duration_ms(right))
    longer = max(_duration_ms(left), _duration_ms(right), 1)
    return shorter / longer >= CONTAINMENT_DURATION_RATIO


class SegmentMerger:
    def __init__(self) -> None:
        self._segments: List[TranscriptSegment] = []

    def add(self, segments: Iterable[TranscriptSegment]) -> List[TranscriptSegment]:
        """Absorb segments and return those the caller should emit.

        The result mixes newly accepted segments with segments that replaced an
        earlier, less complete decode. Replacements keep the original id so the
        client can update the caption it already rendered instead of appending a
        second one.
        """
        emitted: List[TranscriptSegment] = []
        for segment in segments:
            candidate = replace(segment, text=segment.text.strip())
            if not candidate.text:
                continue
            index, existing, superseded = self._find_duplicate(candidate)
            if existing is None:
                self._segments.append(candidate)
                emitted.append(candidate)
                continue
            if not superseded:
                # The stored segment already carries this text.
                continue
            updated = replace(
                existing,
                text=candidate.text,
                start_ms=min(existing.start_ms, candidate.start_ms),
                end_ms=max(existing.end_ms, candidate.end_ms),
            )
            self._segments[index] = updated
            emitted.append(updated)

        self._segments.sort(key=lambda item: (item.start_ms, item.end_ms, item.id))
        return sorted(emitted, key=lambda item: (item.start_ms, item.end_ms, item.id))

    def all_segments(self) -> List[TranscriptSegment]:
        return list(self._segments)

    def _find_duplicate(
        self, candidate: TranscriptSegment
    ) -> Tuple[Optional[int], Optional[TranscriptSegment], bool]:
        """Locate an earlier decode of the same speech.

        Returns the position, the stored segment, and whether the candidate is
        the more complete of the two and should replace it.
        """
        start = max(0, len(self._segments) - COMPARISON_WINDOW)
        for index in range(len(self._segments) - 1, start - 1, -1):
            existing = self._segments[index]
            if _overlap_ms(existing, candidate) < MIN_DUPLICATE_OVERLAP_MS:
                continue
            existing_text = _normalized_text(existing.text)
            candidate_text = _normalized_text(candidate.text)
            if not existing_text or not candidate_text:
                continue
            if existing_text == candidate_text:
                return index, existing, False
            if not _similar_duration(existing, candidate):
                continue
            if existing_text in candidate_text:
                # The candidate decoded a longer span of the same audio.
                return index, existing, True
            if candidate_text in existing_text:
                # The stored decode is already the more complete one.
                return index, existing, False
        return None, None, False
