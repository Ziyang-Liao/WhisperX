from __future__ import annotations
"""Subtitle (SRT / WebVTT) serialization and audio extraction helpers.

Subtitles are produced from a list of timestamped segments. Translation reuses
the *source* segment timings (only the text changes), so timing is always
aligned regardless of target language — see docs/subtitle-platform-design.md §6.
"""

import subprocess
from dataclasses import dataclass


@dataclass
class SubtitleCue:
    """One subtitle line: a time span plus its text."""

    start: float  # seconds
    end: float  # seconds
    text: str


def _format_timestamp(seconds: float, *, vtt: bool) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    sep = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def _clean(text: str) -> str:
    return " ".join(text.strip().split())


def cues_from_segments(segments: list[dict]) -> list[SubtitleCue]:
    """Build cues from transcript/translation segments ({start,end,text})."""
    cues: list[SubtitleCue] = []
    for seg in segments:
        text = _clean(str(seg.get("text", "")))
        if not text:
            continue
        cues.append(
            SubtitleCue(
                start=float(seg.get("start", 0.0)),
                end=float(seg.get("end", 0.0)),
                text=text,
            )
        )
    return cues


def to_srt(cues: list[SubtitleCue]) -> str:
    """Render cues as SubRip (.srt)."""
    blocks = []
    for i, cue in enumerate(cues, start=1):
        start = _format_timestamp(cue.start, vtt=False)
        end = _format_timestamp(cue.end, vtt=False)
        blocks.append(f"{i}\n{start} --> {end}\n{cue.text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_vtt(cues: list[SubtitleCue]) -> str:
    """Render cues as WebVTT (.vtt), the format <video><track> consumes."""
    lines = ["WEBVTT", ""]
    for cue in cues:
        start = _format_timestamp(cue.start, vtt=True)
        end = _format_timestamp(cue.end, vtt=True)
        lines.append(f"{start} --> {end}")
        lines.append(cue.text)
        lines.append("")
    return "\n".join(lines)


def extract_audio(video_path: str, audio_path: str) -> str:
    """Extract a 16 kHz mono WAV from a video/audio file for WhisperX input.

    WhisperX loads audio at 16 kHz; extracting once up front keeps the engine
    input uniform whether the upload was a video or an audio file. Raises
    RuntimeError with ffmpeg's stderr on failure.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",            # drop video
        "-ac", "1",       # mono
        "-ar", "16000",   # 16 kHz
        "-f", "wav",
        audio_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr[-500:]}")
    return audio_path
