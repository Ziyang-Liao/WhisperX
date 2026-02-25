from __future__ import annotations
"""Audio management API routes."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.schemas.audio import AudioFileResponse, PaginatedResponse
from app.schemas.transcript import TranscriptResult
from app.services.audio_manager import AudioManager

router = APIRouter(prefix="/audio", tags=["audio"])


def _get_manager(db: Session = Depends(get_db)) -> AudioManager:
    return AudioManager(db)


@router.post("/upload", response_model=list[AudioFileResponse], status_code=201)
async def upload_audio(
    files: list[UploadFile] = File(...),
    manager: AudioManager = Depends(_get_manager),
):
    """Upload one or more audio files."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    try:
        records = await manager.upload_files(files)
        return [AudioFileResponse.model_validate(r) for r in records]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")


@router.get("", response_model=PaginatedResponse[AudioFileResponse])
async def list_audio(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    sort_by: str = Query("upload_time"),
    sort_order: str = Query("desc"),
    manager: AudioManager = Depends(_get_manager),
):
    """List audio files with pagination, search, and sorting."""
    return await manager.list_audio(
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/{audio_id}", response_model=AudioFileResponse)
async def get_audio(
    audio_id: int,
    manager: AudioManager = Depends(_get_manager),
):
    """Get a single audio file by ID."""
    try:
        record = await manager.get_audio(audio_id)
        return AudioFileResponse.model_validate(record)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Audio file {audio_id} not found")


@router.put("/{audio_id}", response_model=AudioFileResponse)
async def replace_audio(
    audio_id: int,
    file: UploadFile = File(...),
    manager: AudioManager = Depends(_get_manager),
):
    """Replace an existing audio file with a new one."""
    try:
        record = await manager.replace_file(audio_id, file)
        return AudioFileResponse.model_validate(record)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Audio file {audio_id} not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{audio_id}", status_code=204)
async def delete_audio(
    audio_id: int,
    manager: AudioManager = Depends(_get_manager),
):
    """Delete an audio file and its associated data."""
    try:
        await manager.delete_files([audio_id])
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Audio file {audio_id} not found")


@router.get("/{audio_id}/transcript", response_model=TranscriptResult)
async def get_transcript(
    audio_id: int,
    manager: AudioManager = Depends(_get_manager),
):
    """Get the transcription result for an audio file."""
    try:
        record = await manager.get_audio(audio_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Audio file {audio_id} not found")

    if record.transcription_status != "completed" or not record.transcript_json:
        raise HTTPException(
            status_code=404,
            detail="Transcript not available. File has not been transcribed yet.",
        )

    return TranscriptResult.from_json(record.transcript_json)
