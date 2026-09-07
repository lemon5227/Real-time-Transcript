from backend.models import SessionConfig, TranscriptSegment


def test_session_config_rejects_unsupported_sample_rate():
    try:
        SessionConfig(mode="local", model="small", language="en", sample_rate=123)
    except ValueError as exc:
        assert "sample_rate" in str(exc)
    else:
        raise AssertionError("invalid sample rate was accepted")


def test_transcript_segment_serializes_without_provider_objects():
    segment = TranscriptSegment(
        id="segment-1",
        text="Hello",
        start_ms=100,
        end_ms=900,
        is_final=True,
        confidence=0.91,
    )
    assert segment.to_dict() == {
        "id": "segment-1",
        "text": "Hello",
        "start_ms": 100,
        "end_ms": 900,
        "is_final": True,
        "confidence": 0.91,
    }
