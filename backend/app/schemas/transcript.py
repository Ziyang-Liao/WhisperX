from __future__ import annotations
from pydantic import BaseModel


class WordSegment(BaseModel):
    word: str
    start: float
    end: float
    score: float
    speaker: str | None = None


class TranscriptSegment(BaseModel):
    text: str
    start: float
    end: float
    speaker: str | None = None
    words: list[WordSegment]


class TranscriptResult(BaseModel):
    segments: list[TranscriptSegment]
    language: str
    duration: float

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, json_str: str) -> "TranscriptResult":
        """Deserialize from JSON string."""
        return cls.model_validate_json(json_str)

    def to_plain_text(self) -> str:
        """Format as human-readable plain text."""
        lines = []
        for seg in self.segments:
            prefix = f"[{seg.speaker}] " if seg.speaker else ""
            lines.append(f"[{seg.start:.2f}-{seg.end:.2f}] {prefix}{seg.text}")
        return "\n".join(lines)
