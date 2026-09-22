from backend.models import TranscriptSegment
from backend.segment_merger import SegmentMerger, covers_the_same_speech


def segment(identifier, text, start_ms, end_ms):
    return TranscriptSegment(identifier, text, start_ms, end_ms, True, None)


def test_merger_deduplicates_repeated_overlap_text():
    merger = SegmentMerger()
    first = [segment("1", "supervised learning", 0, 1200)]
    second = [segment("2", "supervised learning", 600, 1800)]
    assert [s.text for s in merger.add(first).segments] == ["supervised learning"]
    assert merger.add(second).segments == []


def test_merger_deduplicates_default_window_parameters():
    """The shipped defaults are a 3 s window sliding every 2.5 s.

    The overlap is only 500 ms, well under the old 30 % threshold, so a repeated
    decode used to survive and show up twice in the caption feed.
    """
    merger = SegmentMerger()
    text = "Today we will discuss matrix factorization."
    accepted = merger.add([segment("w0", text, 0, 3000)]).segments
    assert len(accepted) == 1
    assert merger.add([segment("w1", text, 2500, 5500)]).segments == []
    assert len(merger.all_segments()) == 1
    # The next window carries new speech and must still get through.
    following = merger.add([segment("w2", "Then we look at gradient descent.", 5000, 8000)]).segments
    assert [s.text for s in following] == ["Then we look at gradient descent."]
    assert len(merger.all_segments()) == 2


def test_merger_replaces_partial_decode_with_longer_one():
    """A later window often decodes a longer span of the same sentence."""
    merger = SegmentMerger()
    merger.add([segment("1", "Today we will discuss", 0, 3000)])
    updated = merger.add([segment("2", "Today we will discuss matrix factorization", 2500, 5500)]).segments
    # The replacement keeps the original id so the client updates one caption.
    assert [s.id for s in updated] == ["1"]
    assert updated[0].text == "Today we will discuss matrix factorization"
    assert (updated[0].start_ms, updated[0].end_ms) == (0, 5500)
    assert len(merger.all_segments()) == 1


def test_merger_keeps_separate_sentences():
    merger = SegmentMerger()
    merger.add([segment("1", "matrix factorization", 0, 3000)])
    accepted = merger.add([segment("2", "gradient descent", 2500, 5500)]).segments
    assert [s.text for s in accepted] == ["gradient descent"]
    assert len(merger.all_segments()) == 2


def test_merger_keeps_repeated_phrase_spoken_twice():
    """A lecturer repeating a phrase later on is not a window duplicate."""
    merger = SegmentMerger()
    merger.add([segment("1", "Thank you", 0, 1500)])
    accepted = merger.add([segment("2", "Thank you", 20000, 21500)]).segments
    assert len(accepted) == 1
    assert len(merger.all_segments()) == 2


def test_merger_keeps_short_phrase_followed_by_longer_sentence():
    """Containment alone must not merge these: the durations are too different."""
    merger = SegmentMerger()
    merger.add([segment("1", "Okay", 0, 1000)])
    accepted = merger.add([segment("2", "Okay so let's start", 800, 3000)]).segments
    assert [s.id for s in accepted] == ["2"]
    assert len(merger.all_segments()) == 2


def test_merger_collapses_a_sentence_that_grows_across_windows():
    """The live path re-decodes a window ending at the live edge, so the newest
    sentence grows a few words at a time instead of arriving complete."""
    merger = SegmentMerger()
    merger.add([segment("1", "If we select points,", 17800, 18300)])
    merger.add([segment("2", "If we select points, not only", 17800, 20300)])
    updated = merger.add(
        [
            segment(
                "3",
                "If we select points, not only form a line, which is a two-dimensional object.",
                17800,
                26000,
            )
        ]
    ).segments

    assert [s.id for s in updated] == ["1"]
    assert updated[0].text.endswith("two-dimensional object.")
    assert len(merger.all_segments()) == 1


def test_merger_replaces_a_word_revised_between_windows():
    """A later window can fix a word the earlier one guessed wrong."""
    merger = SegmentMerger()
    merger.add([segment("1", "you will use the invisible formula very soon", 114650, 119450)])
    updated = merger.add(
        [segment("2", "you will use the inverse formula very soon", 114730, 122250)]
    ).segments

    assert [s.id for s in updated] == ["1"]
    assert updated[0].text == "you will use the inverse formula very soon"
    assert len(merger.all_segments()) == 1


def test_merger_keeps_two_utterances_that_merely_start_alike():
    """Only overlapping audio marks a re-decode; a later repeat is a real segment."""
    merger = SegmentMerger()
    merger.add([segment("1", "If we select points", 0, 2000)])
    accepted = merger.add([segment("2", "If we select points again", 30000, 32000)]).segments

    assert [s.id for s in accepted] == ["2"]
    assert len(merger.all_segments()) == 2


def test_merger_keeps_one_caption_when_the_overlap_is_only_partial():
    """A re-decode does not have to nest inside the previous one.

    Both of these are the same speech with different boundaries, but the later
    decode starts 2.7s after the earlier one and only 73% of the shorter span is
    shared. A threshold set just above the duplicates missed it and the lecture
    kept two overlapping rows saying the same thing.
    """
    merger = SegmentMerger()
    merger.add(
        [
            segment(
                "1",
                "So that sometimes should be checked, it depends on the on the concrete and the uh rest problem.",
                46640,
                56560,
            )
        ]
    ).segments
    updated = merger.add(
        [
            segment(
                "2",
                "It depends on the on the concrete and the uh that's also a very good question",
                49360,
                61840,
            )
        ]
    ).segments

    assert [s.id for s in updated] == ["1"]
    assert updated[0].text.endswith("very good question")
    assert (updated[0].start_ms, updated[0].end_ms) == (46640, 61840)
    assert len(merger.all_segments()) == 1


def test_merger_absorbs_a_one_word_decode_nested_in_the_next_sentence():
    """A stray "And" spans 80 ms, well under the overlap floor. The floor must
    not be what decides: the longer decode covers that audio entirely."""
    merger = SegmentMerger()
    merger.add([segment("1", "And", 92850, 92930)])
    updated = merger.add(
        [
            segment(
                "2",
                "And when we take the X is the point of our plane, similarly, we can write ax plus D by C.",
                92770,
                104290,
            )
        ]
    ).segments

    assert [s.id for s in updated] == ["1"]
    assert updated[0].text.startswith("And when we take the X")
    assert len(merger.all_segments()) == 1


def test_merger_replaces_a_reworded_decode_of_the_same_speech():
    """The two passes can disagree on the words enough that no text rule fires
    (0.31 similarity here); the audio they cover is the reliable signal."""
    merger = SegmentMerger()
    merger.add([segment("1", "And well, we think the X", 92930, 94850)])
    updated = merger.add(
        [
            segment(
                "2",
                "And when we take the X is the point of our plane, similarly, we can write ax plus D by C.",
                92770,
                104290,
            )
        ]
    ).segments

    assert [s.id for s in updated] == ["1"]
    assert updated[0].text.endswith("ax plus D by C.")
    assert (updated[0].start_ms, updated[0].end_ms) == (92770, 104290)
    assert len(merger.all_segments()) == 1


def test_merger_keeps_one_caption_when_the_onset_drifts():
    """The model's word timings move a few hundred milliseconds between passes.

    An earlier onset tolerance of 300 ms could not see that these two are the
    same sentence, and the lecture ended up with two captions covering the same
    speech. The overlap is what has to decide.
    """
    merger = SegmentMerger()
    merger.add(
        [
            segment(
                "1",
                "Okay, so that sometimes should be checked, it depends on the on the",
                46570,
                50970,
            )
        ]
    ).segments
    updated = merger.add(
        [
            segment(
                "2",
                "So that sometimes should be checked, it depends on the on the concrete and the uh",
                46970,
                54090,
            )
        ]
    ).segments

    assert [s.id for s in updated] == ["1"]
    assert updated[0].text.endswith("the concrete and the uh")
    assert len(merger.all_segments()) == 1


def test_merger_still_keeps_a_short_phrase_followed_by_a_longer_sentence():
    """The coverage rule is what protects "Okay" + "Okay so let's start": the
    longer sentence overlaps only a fifth of the shorter one."""
    merger = SegmentMerger()
    merger.add([segment("1", "Okay", 0, 1000)])
    accepted = merger.add([segment("2", "Okay so let's start", 800, 3000)]).segments

    assert [s.id for s in accepted] == ["2"]
    assert len(merger.all_segments()) == 2


def test_merger_skips_blank_text():
    merger = SegmentMerger()
    assert merger.add([segment("1", "   ", 0, 1000)]).segments == []
    assert merger.all_segments() == []


def test_merger_stays_linear_on_long_sessions():
    """Only neighbouring windows can duplicate, so the tail is enough."""
    merger = SegmentMerger()
    for index in range(500):
        merger.add([segment("s%d" % index, "sentence number %d" % index, index * 4000, index * 4000 + 3000)])
    assert len(merger.all_segments()) == 500


def test_merger_compacts_a_stored_pair_that_grew_into_overlap():
    """``_find_duplicate`` stops at the first match, scanning backwards.

    So a candidate that matches a *later* stored caption is never compared with
    an earlier one — and the match extends the later caption leftwards, into
    that earlier caption's span. Nothing else would ever compare that pair, and
    the finished transcript kept two captions saying the same thing.
    """
    merger = SegmentMerger()
    merger.add([segment("1", "an earlier sentence", 0, 2000)])
    merger.add([segment("2", "a later sentence", 3000, 6000)])

    outcome = merger.add([segment("3", "a later sentence, decoded further back", 0, 7000)])

    # The later caption survives and absorbs the earlier one, and the client is
    # told which id to drop.
    assert outcome.removed_ids == ["1"]
    assert [s.id for s in outcome.segments] == ["2"]
    assert (outcome.segments[0].start_ms, outcome.segments[0].end_ms) == (0, 7000)
    assert len(merger.all_segments()) == 1


def test_merger_compaction_reports_every_caption_it_removes():
    """A removal the client is not told about stays on screen as a duplicate."""
    merger = SegmentMerger()
    merger.add([segment("1", "an earlier sentence", 0, 2000)])
    merger.add([segment("2", "a later sentence", 3000, 6000)])
    outcome = merger.add([segment("3", "a later sentence, decoded further back", 0, 7000)])

    assert outcome.removed_ids == ["1"]
    assert [s.id for s in merger.all_segments()] == ["2"]


def test_merger_compaction_leaves_distinct_sentences_alone():
    """Two captions that merely touch are not the same speech."""
    merger = SegmentMerger()
    merger.add([segment("1", "matrix factorization", 0, 3000)])
    merger.add([segment("2", "gradient descent", 2500, 5500)])
    outcome = merger.add([segment("3", "and then the inverse", 5000, 8000)])

    assert outcome.removed_ids == []
    assert len(merger.all_segments()) == 3


def test_merger_never_leaves_two_captions_covering_the_same_speech():
    """The invariant the compaction pass exists to hold.

    A sliding window re-decodes the newest sentence as it grows, and the model
    returns drifting boundaries every time. Whatever the drift, the transcript
    must never end up with two captions covering the same speech — that is the
    duplicate a reader notices, and it survived into the finished lecture.
    """
    merger = SegmentMerger()
    script = [
        ("a", "the first sentence", 1000, 3000),
        ("b", "the second sentence", 5000, 8000),
        # The window now reaches further back, so the same speech returns with an
        # earlier start — which is what grows it across the first caption.
        ("c", "the second sentence, decoded from further back", 1000, 8100),
        ("d", "a third sentence after that", 9000, 12000),
        ("e", "a third sentence after that, decoded further back", 6000, 12100),
    ]
    for identifier, text, start, end in script:
        merger.add([segment(identifier, text, start, end)])
        stored = merger.all_segments()
        for left in range(len(stored)):
            for right in range(left + 1, len(stored)):
                assert not covers_the_same_speech(stored[left], stored[right]), (
                    "two stored captions cover the same speech: %r and %r"
                    % (stored[left], stored[right])
                )
