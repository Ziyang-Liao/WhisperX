from __future__ import annotations
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class AudioFileResponse(BaseModel):
    id: int
    filename: str
    file_size: int
    duration: float
    format: str
    upload_time: datetime
    transcription_status: str

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
