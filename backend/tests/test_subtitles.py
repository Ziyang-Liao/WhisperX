"""Tests for SRT/VTT serialization and cue building."""

from app.services.subtitles import (
    SubtitleCue,
    _format_timestamp,
    cues_from_segments,
    to_srt,
    to_vtt,
)


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
