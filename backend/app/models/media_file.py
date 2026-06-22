from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class MediaFile(Base):
    """An uploaded video (or audio) file stored in S3.

    Evolves the old audio-only AudioFile: the bytes live in S3 (`s3_key`)
    rather than local disk, `media_type` distinguishes video/audio, and
    `source_language` records the auto-detected (or manually set) source.
    Translated subtitles live in the related SubtitleTrack rows.
    """

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False, default="video")
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    thumbnail_s3_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    source_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # pending -> processing -> completed / failed
    transcription_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    # Source-language transcript with word/segment timestamps (JSON).
    transcript_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    upload_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    subtitle_tracks = relationship(
        "SubtitleTrack",
        back_populates="media_file",
        cascade="all, delete-orphan",
    )
