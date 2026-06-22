"""Tests for SRT/VTT serialization and cue building."""

from app.services.subtitles import (
    MAX_CUE_CHARS,
    SubtitleCue,
    _format_timestamp,
    chunk_into_cues,
    cues_from_segments,
    to_srt,
    to_vtt,
)


def _word(w, s, e):
    return {"word": w, "start": s, "end": e}


def test_chunk_breaks_on_sentence_punctuation():
    # Two sentences in one coarse segment -> two cues, timed by words.
    seg = {
        "start": 0.0, "end": 4.0, "text": "Hello there. How are you?",
        "words": [
            _word("Hello", 0.0, 0.4), _word("there.", 0.5, 0.9),
            _word("How", 2.0, 2.2), _word("are", 2.3, 2.5), _word("you?", 2.6, 3.0),
        ],
    }
    cues = chunk_into_cues([seg])
    assert len(cues) == 2
    assert cues[0].text == "Hello there."
    assert cues[0].start == 0.0 and cues[0].end == 0.9   # from words, not segment
    assert cues[1].text == "How are you?"
    assert cues[1].start == 2.0 and cues[1].end == 3.0


def test_chunk_breaks_on_long_pause():
    # No punctuation, but a >=PAUSE_GAP gap splits the cue.
    seg = {
        "start": 0.0, "end": 10.0, "text": "one two three four",
        "words": [
            _word("one", 0.0, 0.3), _word("two", 0.4, 0.7),
            _word("three", 5.0, 5.3), _word("four", 5.4, 5.7),  # 4.3s gap
        ],
    }
    cues = chunk_into_cues([seg])
    assert len(cues) == 2
    assert cues[0].text == "one two"
    assert cues[1].text == "three four"


def test_chunk_breaks_on_length_cap():
    # A long run of words with no punctuation/pause must still be split by length.
    words = [_word(f"word{i}", i * 0.3, i * 0.3 + 0.25) for i in range(40)]
    seg = {"start": 0.0, "end": 12.0, "text": " ".join(w["word"] for w in words), "words": words}
    cues = chunk_into_cues([seg])
    assert len(cues) > 1
    assert all(len(c.text) <= MAX_CUE_CHARS for c in cues)
    # No content lost: every word appears across the cues, in order.
    joined = " ".join(c.text for c in cues).split()
    assert joined == [w["word"] for w in words]


def test_chunk_cjk_has_no_spaces():
    seg = {
        "start": 0.0, "end": 2.0, "text": "你好世界。",
        "words": [
            _word("你", 0.0, 0.3), _word("好", 0.3, 0.6),
            _word("世", 0.6, 0.9), _word("界。", 0.9, 1.2),
        ],
    }
    cues = chunk_into_cues([seg])
    assert len(cues) == 1
    assert cues[0].text == "你好世界。"  # no spaces between CJK


def test_chunk_fallback_without_words():
    # Segment with no word timings -> kept as a single cue (no content lost).
    seg = {"start": 1.0, "end": 3.0, "text": "no word timings here", "words": []}
    cues = chunk_into_cues([seg])
    assert len(cues) == 1
    assert cues[0].text == "no word timings here"
    assert cues[0].start == 1.0 and cues[0].end == 3.0


def test_timestamp_srt_vs_vtt():
    # 1h 2m 3.456s
    secs = 3723.456
    assert _format_timestamp(secs, vtt=False) == "01:02:03,456"
    assert _format_timestamp(secs, vtt=True) == "01:02:03.456"


def test_timestamp_zero_and_negative():
    assert _format_timestamp(0.0, vtt=False) == "00:00:00,000"
    assert _format_timestamp(-5.0, vtt=True) == "00:00:00.000"


def test_cues_from_segments_skips_empty_and_cleans_whitespace():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "  hello   world "},
        {"start": 1.0, "end": 2.0, "text": "   "},  # dropped
        {"start": 2.0, "end": 3.0, "text": "line\nbreak"},
    ]
    cues = cues_from_segments(segs)
    assert len(cues) == 2
    assert cues[0].text == "hello world"
    assert cues[1].text == "line break"


def test_to_srt_format():
    cues = [
        SubtitleCue(0.0, 2.5, "first"),
        SubtitleCue(2.5, 5.0, "second"),
    ]
    srt = to_srt(cues)
    assert srt == (
        "1\n00:00:00,000 --> 00:00:02,500\nfirst\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\nsecond\n"
    )


def test_to_vtt_format():
    cues = [SubtitleCue(0.0, 2.5, "first")]
    vtt = to_vtt(cues)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:02.500" in vtt
    assert "first" in vtt


def test_empty_cues():
    assert to_srt([]) == ""
    assert to_vtt([]) == "WEBVTT\n"
