# Feature: batch-speech-to-text, Property 14: 转录结果序列化往返一致性
# Feature: batch-speech-to-text, Property 15: 纯文本格式化包含完整信息

from hypothesis import given, settings
from hypothesis import strategies as st

from app.schemas.transcript import TranscriptResult, TranscriptSegment, WordSegment

# --- Strategies ---

word_segment_strategy = st.builds(
    WordSegment,
    word=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N", "P"))),
    start=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
    end=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    speaker=st.one_of(st.none(), st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N")))),
)

transcript_segment_strategy = st.builds(
    TranscriptSegment,
    text=st.text(min_size=1, max_size=200, alphabet=st.characters(categories=("L", "N", "P", "Z"))),
    start=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
    end=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False, allow_infinity=False),
    speaker=st.one_of(st.none(), st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N")))),
    words=st.lists(word_segment_strategy, min_size=0, max_size=10),
)

transcript_result_strategy = st.builds(
    TranscriptResult,
    segments=st.lists(transcript_segment_strategy, min_size=0, max_size=5),
    language=st.sampled_from(["zh", "en", "ja", "ko", "fr", "de", "es"]),
    duration=st.floats(min_value=0.0, max_value=7200.0, allow_nan=False, allow_infinity=False),
)


# --- Property 14: Serialization round-trip consistency ---

@settings(max_examples=100)
@given(result=transcript_result_strategy)
def test_property_14_serialization_round_trip(result: TranscriptResult):
    """Property 14: to_json() -> from_json() produces an equivalent object."""
    json_str = result.to_json()
    restored = TranscriptResult.from_json(json_str)
    assert restored == result


# --- Property 15: Plain text formatting contains complete information ---

@settings(max_examples=100)
@given(result=transcript_result_strategy)
def test_property_15_plain_text_contains_all_info(result: TranscriptResult):
    """Property 15: to_plain_text() output contains all segment text, timestamps, and speaker info."""
    plain = result.to_plain_text()

    for seg in result.segments:
        # Each segment's text must appear in the output
        assert seg.text in plain

        # Timestamps must appear formatted as [start-end]
        timestamp = f"[{seg.start:.2f}-{seg.end:.2f}]"
        assert timestamp in plain

        # If speaker is present, it must appear in the output
        if seg.speaker:
            assert f"[{seg.speaker}]" in plain
