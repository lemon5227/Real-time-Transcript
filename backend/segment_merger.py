"""Merge streamed transcript segments and suppress sliding-window duplicates.

Windowed providers see the same speech twice: consecutive windows overlap by
``AUDIO_OVERLAP_SECONDS`` so a sentence is not cut at the window edge. The
overlap makes the model decode part of the audio twice, and the duplicate has to
be removed here because the provider has no memory of the previous window.

Two properties of the duplicate drive the rules below:

* it always comes from a *neighbouring* window, so only the newest segments can
  match, and
* it describes the same stretch of audio, which is what the rules compare --
  the wording is not reliable, because a later pass over the same speech can
  reword half the sentence.

Timestamps are absolute from here on. Callers place each segment on the
recording timeline before handing it over, so the merger never shifts them.

A stored segment can grow into its neighbour, so ``add()`` also compacts.
Comparing the incoming candidate is not enough: a later decode can *replace* a
stored segment with a fuller span, and that growth can bring two stored segments
into overlap after both were created disjoint. Nothing else would ever compare
that pair, and the lecture kept two overlapping captions saying the same thing.
So every ``add()`` finishes by re-examining the tail, and merging a stored pair
means one of them has to disappear from the client -- which is why ``add()``
returns a ``MergeOutcome`` rather than a plain list. A caller that ignores
``removed_ids`` leaves the duplicate on screen.
"""

import re
from dataclasses import dataclass, field, replace
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

# The live path re-decodes a window that ends at the live edge, so the newest
# sentence comes back several times as it grows: "If we select points," becomes
# "If we select points, not only ...". The duration guard above cannot see that
# these are one utterance -- the earlier decode can be a fifth of the later one
# -- so the history filled up with near-duplicate revisions.
#
# What identifies the re-decode is the audio, not the wording: a later pass can
# reword half the sentence ("And now I highlight that E, B, and C" against "But
# A, B, and C are the plane parameters"), so no text rule catches it reliably.
# The two decodes do cover the same stretch of speech.
#
# Measured on the real lecture, the two populations are far apart: every true
# re-decode covered at least 73% of the shorter span, while a lecturer repeating
# a phrase, or starting a new sentence after "Okay", stayed at or below 20%.
# The threshold sits in that empty gap rather than just above the duplicates, so
# that a re-decode whose boundaries drift a little further still merges.
#
# The onset is deliberately not used: the model's word timings drift by a few
# hundred milliseconds between passes, and a tolerance tight enough to protect
# "Okay" from "Okay so let's start" (800 ms apart) was splitting one sentence
# into two separate captions (400 ms apart).
SAME_SPEECH_COVERAGE = 0.6


def covers_the_same_speech(left: TranscriptSegment, right: TranscriptSegment) -> bool:
    """True when the two segments describe the same stretch of audio.

    Public because the measurement tooling asks the same question of a finished
    transcript: how many captions the reader ends up with still overlap. That
    count needs no reference transcript, so it is the honest readability check.
    """
    shorter = min(_duration_ms(left), _duration_ms(right))
    if shorter <= 0:
        return False
    return _overlap_ms(left, right) / shorter >= SAME_SPEECH_COVERAGE


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


# Merging one pair can leave the merged span overlapping the next caption, so
# compaction repeats until a pass changes nothing. Each pass drops a segment, so
# it always terminates; the cap is only there to make that visible to a reader
# rather than to be reached.
MAX_COMPACTION_PASSES = 32


@dataclass(frozen=True)
class MergeOutcome:
    """Everything ``add()`` decided, including what has to disappear.

    ``segments`` are the captions the client should add or update in place -- a
    replacement keeps the id of the caption it replaces. ``removed_ids`` are
    captions the client must **delete**: compaction merged a stored pair, so one
    of the two ids no longer exists on the server.

    Both are returned together on purpose. A caller that reads only ``segments``
    leaves the dropped caption on screen, which is exactly the duplicate this
    merger exists to prevent, and the type makes that impossible to overlook.
    """

    segments: List[TranscriptSegment] = field(default_factory=list)
    removed_ids: List[str] = field(default_factory=list)


class SegmentMerger:
    def __init__(self) -> None:
        self._segments: List[TranscriptSegment] = []

    def add(self, segments: Iterable[TranscriptSegment]) -> MergeOutcome:
        """Absorb segments and report what the caller should emit.

        The result mixes newly accepted segments with segments that replaced an
        earlier, less complete decode. Replacements keep the original id so the
        client can update the caption it already rendered instead of appending a
        second one. Any id that a later compaction merged away is reported in
        ``removed_ids`` so the client can drop it.
        """
        emitted: List[TranscriptSegment] = []
        removed: List[str] = []
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
        self._compact(emitted, removed)
        return MergeOutcome(
            segments=sorted(emitted, key=lambda item: (item.start_ms, item.end_ms, item.id)),
            removed_ids=removed,
        )

    def all_segments(self) -> List[TranscriptSegment]:
        return list(self._segments)

    def replace_segment(self, segment: TranscriptSegment) -> bool:
        """Replace one stored caption without creating a second history row."""
        for index, current in enumerate(self._segments):
            if current.id == segment.id:
                self._segments[index] = segment
                return True
        return False

    def _compact(self, emitted: List[TranscriptSegment], removed: List[str]) -> None:
        """Merge stored captions that grew into each other.

        ``add()`` only compares the incoming candidate. A stored segment changes
        underneath it when a later decode replaces it with a fuller span, and
        that growth can bring two stored segments into overlap after both were
        created disjoint. No other pass would ever compare that pair, so on the
        real lecture two overlapping captions saying the same thing survived
        into the transcript. This is that pass.

        A merge never retracts a caption that the same ``add()`` call announced,
        so ``emitted`` and ``removed`` cannot name the same id and the caller may
        emit them in any order. The loser is always the caption with the smaller
        ``end_ms``, and a caption that a candidate just grew always ends at that
        candidate's end; for a just-announced caption to lose, a stored neighbour
        would have to reach past the candidate that was compared with it first --
        which is the very comparison that ruled it out. Checked by search: 300k
        random ``add()`` calls, compaction fired in 8.3% of them, and not one
        announced and retracted the same id.
        """
        for _ in range(MAX_COMPACTION_PASSES):
            pair = self._find_stored_duplicate()
            if pair is None:
                return
            first, second = pair
            # The fuller decode wins, exactly as it does on the add path: what
            # makes a decode fuller is how much audio it covers.
            winner, loser = (first, second) if first.end_ms >= second.end_ms else (second, first)
            merged = replace(
                winner,
                start_ms=min(first.start_ms, second.start_ms),
                end_ms=max(first.end_ms, second.end_ms),
            )
            self._segments[self._segments.index(winner)] = merged
            self._segments.remove(loser)
            removed.append(loser.id)
            self._replace_emitted(emitted, merged)
            self._segments.sort(key=lambda item: (item.start_ms, item.end_ms, item.id))

    def _find_stored_duplicate(
        self,
    ) -> Optional[Tuple[TranscriptSegment, TranscriptSegment]]:
        """Two stored captions that now cover the same speech."""
        start = max(0, len(self._segments) - COMPARISON_WINDOW)
        for left in range(start, len(self._segments)):
            for right in range(left + 1, len(self._segments)):
                if covers_the_same_speech(self._segments[left], self._segments[right]):
                    return self._segments[left], self._segments[right]
        return None

    @staticmethod
    def _replace_emitted(
        emitted: List[TranscriptSegment], merged: TranscriptSegment
    ) -> None:
        for position, item in enumerate(emitted):
            if item.id == merged.id:
                emitted[position] = merged
                return
        emitted.append(merged)

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
            if covers_the_same_speech(existing, candidate):
                # Same speech, decoded again. The fuller decode wins, and what
                # makes a decode fuller is how much audio it covers rather than
                # how long the text is: a later pass can fix a word and come
                # back slightly shorter.
                return index, existing, candidate.end_ms > existing.end_ms
            existing_text = _normalized_text(existing.text)
            candidate_text = _normalized_text(candidate.text)
            if not existing_text or not candidate_text:
                continue
            if _overlap_ms(existing, candidate) < MIN_DUPLICATE_OVERLAP_MS:
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
