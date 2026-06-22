from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class SubtitleTrack(Base):
    """One subtitle track for a media file, in a single language.

    The source-language track (`is_source=True`) is produced directly from
    transcription; every other track is produced by translating the source
    segments. `status` doubles as the work queue for the translation stage.
    """

    __tablename__ = "subtitle_tracks"
    __table_args__ = (
        UniqueConstraint("media_file_id", "language", name="uq_media_language"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_files.id"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    is_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # pending -> processing -> completed / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    srt_s3_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    vtt_s3_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    media_file = relationship("MediaFile", back_populates="subtitle_tracks")
