from __future__ import annotations
"""Subtitle (SRT / WebVTT) serialization and audio extraction helpers.

WhisperX emits coarse ASR segments (sometimes 10-30s of speech as one block).
Showing one such segment as a single subtitle cue dumps a wall of text on screen
that is not in sync with speech. Instead we re-chunk each segment into short cues
using the *word-level* timestamps WhisperX produces (wav2vec2 alignment), breaking
at sentence punctuation, natural pauses, and length caps — so each cue appears as
it is spoken (movie-style). Translation runs on those short cues and reuses their
timings, so every language stays in sync — see docs/subtitle-platform-design.md §6.
"""

import re
import subprocess
from dataclasses import dataclass

# Cue-chunking limits (tuned for readable, movie-style subtitles).
MAX_CUE_CHARS = 42          # latin chars per cue (~one line)
MAX_CJK_CHARS = 20          # CJK is denser; fewer chars per cue
MAX_CUE_DURATION = 6.0      # seconds a single cue stays on screen
PAUSE_GAP = 0.7             # a gap >= this between words starts a new cue
SENTENCE_END = ".!?。！？…"   # break after these (cue boundary)
CLAUSE_END = ",;:，、；："      # prefer breaking here when over length

_CJK_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]"
)


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


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


def _join_words(words: list[dict]) -> str:
    """Join word tokens into text, without spaces between CJK characters."""
    parts = [str(w.get("word", "")).strip() for w in words]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    out = parts[0]
    for prev, cur in zip(parts, parts[1:]):
        # No space if either side of the boundary is CJK (CJK has no word spacing).
        if _is_cjk(prev[-1]) or _is_cjk(cur[0]):
            out += cur
        else:
            out += " " + cur
    return out


def _flush_cue(words: list[dict]) -> SubtitleCue | None:
    text = _clean(_join_words(words))
    if not text:
        return None
    starts = [float(w["start"]) for w in words if w.get("start") is not None]
    ends = [float(w["end"]) for w in words if w.get("end") is not None]
    if not starts or not ends:
        return None
    return SubtitleCue(start=min(starts), end=max(ends), text=text)


def chunk_into_cues(segments: list[dict]) -> list[SubtitleCue]:
    """Split coarse segments into short, word-timed cues (movie-style).

    Each segment is expected to carry a ``words`` list of {word,start,end}. Cues
    break at: sentence-ending punctuation, a pause >= PAUSE_GAP between words, or
    when the accumulated text would exceed the per-cue character / duration cap
    (preferring a recent clause boundary). Cue timings come from the contained
    words, so a cue appears exactly when its words are spoken.

    Falls back to one cue per segment when a segment has no usable word timings
    (e.g. alignment was skipped), so we never lose content.
    """
    cues: list[SubtitleCue] = []
    for seg in segments:
        words = [
            w for w in (seg.get("words") or [])
            if w.get("start") is not None and w.get("end") is not None and str(w.get("word", "")).strip()
        ]
        if not words:
            # No word timings — keep the segment as a single cue (graceful fallback).
            text = _clean(str(seg.get("text", "")))
            if text:
                cues.append(
                    SubtitleCue(
                        start=float(seg.get("start", 0.0)),
                        end=float(seg.get("end", 0.0)),
                        text=text,
                    )
                )
            continue

        cjk = _is_cjk(seg.get("text", "") or _join_words(words))
        max_chars = MAX_CJK_CHARS if cjk else MAX_CUE_CHARS

        cur: list[dict] = []
        last_clause_idx = -1  # index within cur of the last clause-boundary word
        for i, w in enumerate(words):
            token = str(w["word"]).strip()
            prev = cur[-1] if cur else None

            # Pause before this word -> break the current cue first.
            if prev is not None:
                gap = float(w["start"]) - float(prev["end"])
                if gap >= PAUSE_GAP:
                    c = _flush_cue(cur)
                    if c:
                        cues.append(c)
                    cur, last_clause_idx = [], -1

            cur.append(w)

            # Would the cue get too long (chars or duration)? Break, preferring a
            # recent clause boundary so we don't cut mid-phrase.
            cur_text = _join_words(cur)
            duration = float(cur[-1]["end"]) - float(cur[0]["start"])
            too_long = len(cur_text) > max_chars or duration > MAX_CUE_DURATION
            if too_long and len(cur) > 1:
                if 0 <= last_clause_idx < len(cur) - 1:
                    head, tail = cur[: last_clause_idx + 1], cur[last_clause_idx + 1 :]
                else:
                    head, tail = cur[:-1], cur[-1:]
                c = _flush_cue(head)
                if c:
                    cues.append(c)
                cur, last_clause_idx = tail, -1

            # Track clause boundaries; break immediately on sentence enders.
            if token and token[-1] in CLAUSE_END:
                last_clause_idx = len(cur) - 1
            if token and token[-1] in SENTENCE_END:
                c = _flush_cue(cur)
                if c:
                    cues.append(c)
                cur, last_clause_idx = [], -1

        c = _flush_cue(cur)
        if c:
            cues.append(c)
    return cues


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
