from __future__ import annotations
"""Audio Manager service for file upload, validation, replacement, deletion, and querying."""

import math
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.audio_file import AudioFile
from app.models.task_file_record import TaskFileRecord
from app.schemas.audio import AudioFileResponse, PaginatedResponse

SUPPORTED_FORMATS = {"wav", "mp3", "flac", "m4a", "ogg"}
UPLOAD_DIR = Path("data/uploads")


class AudioManager:
    """Manages audio file CRUD operations, format validation, and querying."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def validate_format(filename: str) -> bool:
        """Check if the file extension is in the supported formats list."""
        if not filename or "." not in filename:
            return False
        ext = filename.rsplit(".", 1)[-1].lower()
        return ext in SUPPORTED_FORMATS

    async def upload_files(self, files: list[UploadFile]) -> list[AudioFile]:
        """Validate format, store files, extract metadata, create DB records."""
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        results: list[AudioFile] = []
        stored_paths: list[Path] = []

        try:
            for file in files:
                if not self.validate_format(file.filename or ""):
                    raise ValueError(
                        f"Unsupported format: {file.filename}. "
                        f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
                    )

                # Generate unique stored filename
                ext = file.filename.rsplit(".", 1)[-1].lower()
                stored_name = f"{uuid.uuid4().hex}.{ext}"
                stored_path = UPLOAD_DIR / stored_name

                # Write file to disk
                content = await file.read()
                stored_path.write_bytes(content)
                stored_paths.append(stored_path)

                file_size = len(content)
                duration = await self._extract_duration(stored_path, ext)

                record = AudioFile(
                    filename=file.filename,
                    stored_path=str(stored_path),
                    file_size=file_size,
                    duration=duration,
                    format=ext,
                    upload_time=datetime.now(timezone.utc),
                    transcription_status="pending",
                )
                self.db.add(record)
                results.append(record)

            self.db.commit()
            for r in results:
                self.db.refresh(r)
            return results

        except Exception:
            # Rollback: remove any files already stored
            for p in stored_paths:
                if p.exists():
                    p.unlink()
            self.db.rollback()
            raise

    @staticmethod
    async def _extract_duration(file_path: Path, ext: str) -> float:
        """Extract audio duration. Returns estimated duration based on file size as fallback."""
        try:
            import mutagen

            audio = mutagen.File(str(file_path))
            if audio is not None and audio.info is not None:
                return float(audio.info.length)
        except Exception:
            pass
        # Fallback: estimate from file size (rough: assume 128kbps)
        file_size = file_path.stat().st_size
        return max(0.1, file_size / (128 * 1024 / 8))

    async def replace_file(self, audio_id: int, new_file: UploadFile) -> AudioFile:
        """Replace an audio file. Rejects if currently being transcribed."""
        record = self.db.query(AudioFile).filter_by(id=audio_id).first()
        if record is None:
            raise FileNotFoundError(f"Audio file with id {audio_id} not found")

        if record.transcription_status == "processing":
            raise PermissionError("Cannot replace file while transcription is in progress")

        if not self.validate_format(new_file.filename or ""):
            raise ValueError(
                f"Unsupported format: {new_file.filename}. "
                f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
            )

        # Remove old physical file
        old_path = Path(record.stored_path)
        if old_path.exists():
            old_path.unlink()

        # Store new file
        ext = new_file.filename.rsplit(".", 1)[-1].lower()
        stored_name = f"{uuid.uuid4().hex}.{ext}"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_path = UPLOAD_DIR / stored_name

        content = await new_file.read()
        stored_path.write_bytes(content)

        file_size = len(content)
        duration = await self._extract_duration(stored_path, ext)

        # Update record metadata
        record.filename = new_file.filename
        record.stored_path = str(stored_path)
        record.file_size = file_size
        record.duration = duration
        record.format = ext
        record.transcription_status = "pending"
        record.transcript_json = None
        record.error_message = None

        self.db.commit()
        self.db.refresh(record)
        return record

    async def delete_files(self, audio_ids: list[int]) -> None:
        """Delete audio files, their physical files, and associated task records."""
        records = self.db.query(AudioFile).filter(AudioFile.id.in_(audio_ids)).all()

        for record in records:
            if record.transcription_status == "processing":
                raise PermissionError(
                    f"Cannot delete file '{record.filename}' (id={record.id}) "
                    "while transcription is in progress"
                )

        for record in records:
            # Delete associated task file records
            self.db.query(TaskFileRecord).filter_by(audio_file_id=record.id).delete()

            # Delete physical file
            stored = Path(record.stored_path)
            if stored.exists():
                stored.unlink()

            # Delete DB record
            self.db.delete(record)

        self.db.commit()

    async def get_audio(self, audio_id: int) -> AudioFile:
        """Get a single audio file by ID."""
        record = self.db.query(AudioFile).filter_by(id=audio_id).first()
        if record is None:
            raise FileNotFoundError(f"Audio file with id {audio_id} not found")
        return record

    async def list_audio(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        sort_by: str = "upload_time",
        sort_order: str = "desc",
    ) -> PaginatedResponse[AudioFileResponse]:
        """Paginated query with optional search and sorting."""
        query = self.db.query(AudioFile)

        # Search: fuzzy match on filename or transcript text
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    AudioFile.filename.ilike(pattern),
                    AudioFile.transcript_json.ilike(pattern),
                )
            )

        # Sorting
        sort_columns = {
            "upload_time": AudioFile.upload_time,
            "filename": AudioFile.filename,
            "transcription_status": AudioFile.transcription_status,
            "duration": AudioFile.duration,
        }
        sort_col = sort_columns.get(sort_by, AudioFile.upload_time)
        if sort_order == "asc":
            query = query.order_by(sort_col.asc())
        else:
            query = query.order_by(sort_col.desc())

        total = query.count()
        total_pages = max(1, math.ceil(total / page_size))
        items = query.offset((page - 1) * page_size).limit(page_size).all()

        return PaginatedResponse(
            items=[AudioFileResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
