from __future__ import annotations
from datetime import datetime

from pydantic import BaseModel


class SubtitleTrackResponse(BaseModel):
    id: int
    language: str
    is_source: bool
    status: str
    error_message: str | None = None

    model_config = {"from_attributes": True}


class MediaFileResponse(BaseModel):
    id: int
    filename: str
    media_type: str
    file_size: int
    duration: float
    format: str
    source_language: str | None = None
    transcription_status: str
    error_message: str | None = None
    upload_time: datetime
    subtitle_tracks: list[SubtitleTrackResponse] = []

    model_config = {"from_attributes": True}


class CreateMediaRequest(BaseModel):
    filename: str
    content_type: str | None = None


class CreateMediaResponse(BaseModel):
    media_id: int
    upload_url: str
    s3_key: str


class GenerateSubtitlesRequest(BaseModel):
    target_languages: list[str]


class BatchGenerateRequest(BaseModel):
    media_ids: list[int]
    target_languages: list[str]
