import pytest

from backend.diarization import SpeakerTimeline, SpeakerTurn
from backend.models import TranscriptSegment


def test_speaker_turn_rejects_invalid_interval_and_confidence():
    with pytest.raises(ValueError, match="timestamps"):
        SpeakerTurn("speaker_0", 800, 400, 0.9)
    with pytest.raises(ValueError, match="confidence"):
        SpeakerTurn("speaker_0", 0, 400, 1.1)


def test_timeline_attributes_a_segment_to_the_speaker_with_majority_overlap():
    timeline = SpeakerTimeline()
    timeline.add_turns([
        SpeakerTurn("speaker_1", 0, 900, 0.92),
        SpeakerTurn("speaker_0", 900, 1600, 0.85),
    ])

    speaker_id, confidence = timeline.attribute(
        TranscriptSegment("segment-1", "lecture", 100, 1000, True)
    )

    assert speaker_id == "speaker_1"
    assert confidence == pytest.approx(0.92)


def test_timeline_does_not_guess_when_two_speakers_are_ambiguous():
    timeline = SpeakerTimeline()
    timeline.add_turns([
        SpeakerTurn("speaker_0", 0, 500, 0.9),
        SpeakerTurn("speaker_1", 500, 1000, 0.9),
    ])

    assert timeline.attribute(TranscriptSegment("segment-1", "lecture", 0, 1000, True)) is None


def test_late_turns_produce_one_update_and_do_not_repeat_it():
    timeline = SpeakerTimeline()
    segment = TranscriptSegment("segment-1", "lecture", 1000, 2000, True)

    assert timeline.updates_for_segments([segment]) == []

    timeline.add_turns([SpeakerTurn("speaker_0", 900, 2100, 0.88)])
    updates = timeline.updates_for_segments([segment])

    assert len(updates) == 1
    assert updates[0].speaker_id == "speaker_0"
    assert updates[0].speaker_confidence == pytest.approx(0.88)
    assert timeline.updates_for_segments([updates[0]]) == []


def test_turns_are_sorted_and_duplicate_same_speaker_turns_are_compacted():
    timeline = SpeakerTimeline()
    timeline.add_turns([
        SpeakerTurn("speaker_0", 1000, 1500, 0.8),
        SpeakerTurn("speaker_0", 400, 1100, 0.9),
        SpeakerTurn("speaker_1", 1700, 1800, 0.7),
    ])

    assert [(turn.speaker_id, turn.start_ms, turn.end_ms) for turn in timeline.turns] == [
        ("speaker_0", 400, 1500),
        ("speaker_1", 1700, 1800),
    ]
