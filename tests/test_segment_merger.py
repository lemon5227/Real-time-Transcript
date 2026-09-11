from backend.models import TranscriptSegment
from backend.segment_merger import SegmentMerger


def segment(identifier, text, start_ms, end_ms):
    return TranscriptSegment(identifier, text, start_ms, end_ms, True, None)


def test_merger_deduplicates_repeated_overlap_text():
    merger = SegmentMerger()
    first = [segment("1", "supervised learning", 0, 1200)]
    second = [segment("2", "supervised learning", 600, 1800)]
    assert [s.text for s in merger.add(first)] == ["supervised learning"]
    assert merger.add(second) == []


def test_merger_deduplicates_default_window_parameters():
    """The shipped defaults are a 3 s window sliding every 2.5 s.

    The overlap is only 500 ms, well under the old 30 % threshold, so a repeated
    decode used to survive and show up twice in the caption feed.
    """
    merger = SegmentMerger()
    text = "Today we will discuss matrix factorization."
    accepted = merger.add([segment("w0", text, 0, 3000)])
    assert len(accepted) == 1
    assert merger.add([segment("w1", text, 2500, 5500)]) == []
    assert len(merger.all_segments()) == 1
    # The next window carries new speech and must still get through.
    following = merger.add([segment("w2", "Then we look at gradient descent.", 5000, 8000)])
    assert [s.text for s in following] == ["Then we look at gradient descent."]
    assert len(merger.all_segments()) == 2


def test_merger_replaces_partial_decode_with_longer_one():
    """A later window often decodes a longer span of the same sentence."""
    merger = SegmentMerger()
    merger.add([segment("1", "Today we will discuss", 0, 3000)])
    updated = merger.add([segment("2", "Today we will discuss matrix factorization", 2500, 5500)])
    # The replacement keeps the original id so the client updates one caption.
    assert [s.id for s in updated] == ["1"]
    assert updated[0].text == "Today we will discuss matrix factorization"
    assert (updated[0].start_ms, updated[0].end_ms) == (0, 5500)
    assert len(merger.all_segments()) == 1


def test_merger_keeps_separate_sentences():
    merger = SegmentMerger()
    merger.add([segment("1", "matrix factorization", 0, 3000)])
    accepted = merger.add([segment("2", "gradient descent", 2500, 5500)])
    assert [s.text for s in accepted] == ["gradient descent"]
    assert len(merger.all_segments()) == 2


def test_merger_keeps_repeated_phrase_spoken_twice():
    """A lecturer repeating a phrase later on is not a window duplicate."""
    merger = SegmentMerger()
    merger.add([segment("1", "Thank you", 0, 1500)])
    accepted = merger.add([segment("2", "Thank you", 20000, 21500)])
    assert len(accepted) == 1
    assert len(merger.all_segments()) == 2


def test_merger_keeps_short_phrase_followed_by_longer_sentence():
    """Containment alone must not merge these: the durations are too different."""
    merger = SegmentMerger()
    merger.add([segment("1", "Okay", 0, 1000)])
    accepted = merger.add([segment("2", "Okay so let's start", 800, 3000)])
    assert [s.id for s in accepted] == ["2"]
    assert len(merger.all_segments()) == 2


def test_merger_skips_blank_text():
    merger = SegmentMerger()
    assert merger.add([segment("1", "   ", 0, 1000)]) == []
    assert merger.all_segments() == []


def test_merger_stays_linear_on_long_sessions():
    """Only neighbouring windows can duplicate, so the tail is enough."""
    merger = SegmentMerger()
    for index in range(500):
        merger.add([segment("s%d" % index, "sentence number %d" % index, index * 4000, index * 4000 + 3000)])
    assert len(merger.all_segments()) == 500
