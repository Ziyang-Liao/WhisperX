from __future__ import annotations
"""Media (video/audio) management + subtitle generation API.

Large uploads use presigned S3 PUT URLs so video bytes never pass through the
API process. The flow is: POST /media (get presigned URL) -> client PUTs to S3
-> POST /media/{id}/complete (validate + enqueue transcription).
"""

import math
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.media_file import MediaFile
from app.models.subtitle_track import SubtitleTrack
from app.schemas.audio import PaginatedResponse
from app.schemas.media import (
    BatchGenerateRequest,
    CreateMediaRequest,
    CreateMediaResponse,
    GenerateSubtitlesRequest,
    MediaFileResponse,
    SubtitleTrackResponse,
)
from app.services import storage as storage_keys
from app.services.storage import StorageService

router = APIRouter(prefix="/media", tags=["media"])

SUPPORTED_FORMATS = {"mp4", "mov", "mkv", "webm", "avi", "m4v",  # video
                     "wav", "mp3", "flac", "m4a", "ogg"}          # audio
VIDEO_FORMATS = {"mp4", "mov", "mkv", "webm", "avi", "m4v"}

# Storage is a process-wide singleton; overridable in tests via set_storage().
_storage: StorageService | None = None


def set_storage(storage: StorageService | None) -> None:
    global _storage
    _storage = storage


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService.from_env()
    return _storage


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.post("", response_model=CreateMediaResponse, status_code=201)
def create_media(req: CreateMediaRequest, db: Session = Depends(get_db)):
    """Create a media record and return a presigned S3 URL for direct upload."""
    ext = _ext(req.filename)
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: .{ext}. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        )
    media_type = "video" if ext in VIDEO_FORMATS else "audio"
    record = MediaFile(filename=req.filename, media_type=media_type, s3_key="", format=ext,
                       transcription_status="created")
    db.add(record)
    db.commit()
    db.refresh(record)

    key = storage_keys.video_key(record.id, ext)
    record.s3_key = key
    db.commit()

    url = get_storage().presigned_put(key, content_type=req.content_type)
    return CreateMediaResponse(media_id=record.id, upload_url=url, s3_key=key)


@router.post("/{media_id}/complete", response_model=MediaFileResponse)
def complete_upload(media_id: int, db: Session = Depends(get_db)):
    """Confirm the client finished uploading; validate object and enqueue."""
    record = db.query(MediaFile).filter_by(id=media_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media {media_id} not found")

    head = get_storage().head(record.s3_key)
    if head is None:
        raise HTTPException(status_code=400, detail="Upload not found in storage")
    record.file_size = head.get("ContentLength", 0)
    record.transcription_status = "pending"  # picked up by the worker
    db.commit()
    db.refresh(record)
    return MediaFileResponse.model_validate(record)


@router.get("", response_model=PaginatedResponse[MediaFileResponse])
def list_media(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(MediaFile).filter(MediaFile.transcription_status != "created").order_by(
        MediaFile.upload_time.desc()
    )
    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedResponse(
        items=[MediaFileResponse.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.get("/{media_id}", response_model=MediaFileResponse)
def get_media(media_id: int, db: Session = Depends(get_db)):
    record = db.query(MediaFile).filter_by(id=media_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media {media_id} not found")
    return MediaFileResponse.model_validate(record)


@router.get("/{media_id}/stream")
def stream_media(media_id: int, db: Session = Depends(get_db)):
    """Return a short-lived presigned GET URL for video playback."""
    record = db.query(MediaFile).filter_by(id=media_id).first()
    if record is None or not record.s3_key:
        raise HTTPException(status_code=404, detail=f"Media {media_id} not found")
    return {"url": get_storage().presigned_get(record.s3_key)}


@router.delete("/{media_id}", status_code=204)
def delete_media(media_id: int, db: Session = Depends(get_db)):
    record = db.query(MediaFile).filter_by(id=media_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media {media_id} not found")
    if record.transcription_status == "processing":
        raise HTTPException(status_code=409, detail="Cannot delete while transcription is in progress")
    get_storage().delete_prefixes(storage_keys.media_prefixes(media_id))
    db.query(SubtitleTrack).filter_by(media_file_id=media_id).delete()
    db.delete(record)
    db.commit()


@router.post("/{media_id}/subtitles", response_model=list[SubtitleTrackResponse], status_code=201)
def generate_subtitles(media_id: int, req: GenerateSubtitlesRequest, db: Session = Depends(get_db)):
    """Queue translation into the given target languages (multi-select).

    Target == source is skipped (the source track already covers it).
    """
    record = db.query(MediaFile).filter_by(id=media_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media {media_id} not found")
    tracks = _enqueue_languages(db, record, req.target_languages)
    db.commit()
    return [SubtitleTrackResponse.model_validate(t) for t in tracks]


@router.get("/{media_id}/subtitles", response_model=list[SubtitleTrackResponse])
def list_subtitles(media_id: int, db: Session = Depends(get_db)):
    tracks = db.query(SubtitleTrack).filter_by(media_file_id=media_id).all()
    return [SubtitleTrackResponse.model_validate(t) for t in tracks]


@router.get("/{media_id}/subtitles/{language}/{fmt}")
def download_subtitle(media_id: int, language: str, fmt: str, db: Session = Depends(get_db)):
    """Return a presigned URL for a generated subtitle file (srt|vtt)."""
    fmt = fmt.lower()
    if fmt not in ("srt", "vtt"):
        raise HTTPException(status_code=400, detail="format must be srt or vtt")
    track = db.query(SubtitleTrack).filter_by(media_file_id=media_id, language=language).first()
    if track is None or track.status != "completed":
        raise HTTPException(status_code=404, detail="Subtitle not available")
    key = track.srt_s3_key if fmt == "srt" else track.vtt_s3_key
    if not key:
        raise HTTPException(status_code=404, detail="Subtitle not available")
    return {"url": get_storage().presigned_get(key)}


@router.post("/subtitles/batch", response_model=list[SubtitleTrackResponse], status_code=201)
def batch_generate(req: BatchGenerateRequest, db: Session = Depends(get_db)):
    """Queue the same target languages across multiple media at once."""
    out: list[SubtitleTrack] = []
    records = db.query(MediaFile).filter(MediaFile.id.in_(req.media_ids)).all()
    for record in records:
        out.extend(_enqueue_languages(db, record, req.target_languages))
    db.commit()
    return [SubtitleTrackResponse.model_validate(t) for t in out]


def _enqueue_languages(db: Session, media: MediaFile, languages: list[str]) -> list[SubtitleTrack]:
    """Create pending non-source tracks for each requested language (idempotent)."""
    created: list[SubtitleTrack] = []
    for lang in languages:
        lang = lang.strip()
        if not lang or lang == media.source_language:
            continue  # source already covers it
        existing = (
            db.query(SubtitleTrack).filter_by(media_file_id=media.id, language=lang).first()
        )
        if existing is not None:
            if existing.status == "failed":
                existing.status = "pending"  # retry
                existing.error_message = None
            created.append(existing)
            continue
        track = SubtitleTrack(media_file_id=media.id, language=lang, is_source=False, status="pending")
        db.add(track)
        created.append(track)
    return created
