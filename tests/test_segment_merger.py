from backend.models import TranscriptSegment
from backend.segment_merger import SegmentMerger


def test_merger_deduplicates_repeated_overlap_text():
    merger = SegmentMerger()
    first = [TranscriptSegment("1", "supervised learning", 0, 1200, True, None)]
    second = [TranscriptSegment("2", "supervised learning", 0, 1200, True, None)]
    assert [s.text for s in merger.add(first, 0)] == ["supervised learning"]
    assert merger.add(second, 600) == []
