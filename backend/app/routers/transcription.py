from __future__ import annotations
"""Transcription task management API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.schemas.audio import PaginatedResponse
from app.schemas.transcription import TranscriptionTaskResponse, TranscriptionTriggerRequest
from app.services.batch_processor import BatchProcessor
from app.services.transcription_engine import TranscriptionEngine

router = APIRouter(prefix="/transcription", tags=["transcription"])


def _get_processor(db: Session = Depends(get_db)) -> BatchProcessor:
    engine = TranscriptionEngine()
    return BatchProcessor(db, engine)


@router.post("/trigger", response_model=TranscriptionTaskResponse, status_code=201)
def trigger_transcription(
    request: TranscriptionTriggerRequest | None = None,
    processor: BatchProcessor = Depends(_get_processor),
):
    """Trigger a batch transcription task. Optionally specify file_ids."""
    file_ids = request.file_ids if request else None
    try:
        task = processor.run_batch(file_ids=file_ids, trigger_type="manual")
        return TranscriptionTaskResponse.model_validate(task)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription trigger failed: {exc}")


@router.get("/tasks", response_model=PaginatedResponse[TranscriptionTaskResponse])
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    processor: BatchProcessor = Depends(_get_processor),
):
    """List transcription tasks with pagination."""
    return processor.list_tasks(page=page, page_size=page_size)


@router.get("/tasks/{task_id}", response_model=TranscriptionTaskResponse)
def get_task(
    task_id: int,
    processor: BatchProcessor = Depends(_get_processor),
):
    """Get a single transcription task by ID."""
    try:
        task = processor.get_task(task_id)
        return TranscriptionTaskResponse.model_validate(task)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
