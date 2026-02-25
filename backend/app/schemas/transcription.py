from __future__ import annotations
from datetime import datetime

from pydantic import BaseModel


class TranscriptionTaskResponse(BaseModel):
    id: int
    trigger_type: str
    status: str
    total_files: int
    processed_files: int
    success_count: int
    failure_count: int
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: float | None

    model_config = {"from_attributes": True}


class TranscriptionTriggerRequest(BaseModel):
    file_ids: list[int] | None = None
