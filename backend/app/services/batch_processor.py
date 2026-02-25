from __future__ import annotations
"""Batch Processor service for managing batch transcription tasks."""

import logging
import math
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.audio_file import AudioFile
from app.models.task_file_record import TaskFileRecord
from app.models.transcription_task import TranscriptionTask
from app.schemas.audio import PaginatedResponse
from app.schemas.transcription import TranscriptionTaskResponse
from app.services.transcription_engine import TranscriptionEngine

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Executes batch transcription tasks with mutual exclusion."""

    def __init__(self, db: Session, engine: TranscriptionEngine):
        self.db = db
        self.engine = engine
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        """Check if there is a currently running batch task."""
        running = (
            self.db.query(TranscriptionTask)
            .filter(TranscriptionTask.status == "running")
            .first()
        )
        return running is not None

    def run_batch(
        self, file_ids: list[int] | None = None, trigger_type: str = "manual"
    ) -> TranscriptionTask:
        """
        Execute a batch transcription task.

        Args:
            file_ids: Specific file IDs to process. None means all pending files.
            trigger_type: 'manual' or 'scheduled'.

        Returns:
            The completed TranscriptionTask record.

        Raises:
            PermissionError: If another batch task is already running.
        """
        with self._lock:
            if self.is_running():
                raise PermissionError(
                    "A batch transcription task is already running. "
                    "Please wait for it to complete."
                )

            # Collect files to process
            if file_ids is not None:
                audio_files = (
                    self.db.query(AudioFile)
                    .filter(AudioFile.id.in_(file_ids))
                    .all()
                )
            else:
                audio_files = (
                    self.db.query(AudioFile)
                    .filter(AudioFile.transcription_status.in_(["pending", "failed"]))
                    .all()
                )

            # Create task record
            task = TranscriptionTask(
                trigger_type=trigger_type,
                status="running",
                total_files=len(audio_files),
                processed_files=0,
                success_count=0,
                failure_count=0,
                started_at=datetime.now(timezone.utc),
            )
            self.db.add(task)
            self.db.commit()
            self.db.refresh(task)

            # Create file records for each audio file
            for af in audio_files:
                tfr = TaskFileRecord(
                    task_id=task.id,
                    audio_file_id=af.id,
                    status="pending",
                )
                self.db.add(tfr)
            self.db.commit()

        # Process files one by one (outside the lock so is_running() still works)
        for af in audio_files:
            tfr = (
                self.db.query(TaskFileRecord)
                .filter_by(task_id=task.id, audio_file_id=af.id)
                .first()
            )
            tfr.status = "processing"
            tfr.started_at = datetime.now(timezone.utc)
            af.transcription_status = "processing"
            self.db.commit()

            try:
                result = self.engine.transcribe_with_retry(af.stored_path)
                af.transcription_status = "completed"
                af.transcript_json = result.to_json()
                af.error_message = None
                tfr.status = "completed"
                task.success_count += 1
            except Exception as exc:
                logger.error("Transcription failed for file %s: %s", af.id, exc)
                af.transcription_status = "failed"
                af.error_message = str(exc)
                tfr.status = "failed"
                tfr.error_message = str(exc)
                task.failure_count += 1

            tfr.completed_at = datetime.now(timezone.utc)
            task.processed_files += 1
            self.db.commit()

        # Finalize task
        task.status = "completed"
        now = datetime.now(timezone.utc)
        task.completed_at = now
        # Handle both timezone-aware and naive datetimes from SQLite
        started = task.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        task.duration_seconds = (now - started).total_seconds()
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: int) -> TranscriptionTask:
        """Get a single transcription task by ID."""
        task = (
            self.db.query(TranscriptionTask)
            .filter_by(id=task_id)
            .first()
        )
        if task is None:
            raise FileNotFoundError(f"Transcription task with id {task_id} not found")
        return task

    def list_tasks(
        self, page: int = 1, page_size: int = 20
    ) -> PaginatedResponse[TranscriptionTaskResponse]:
        """Paginated query of transcription tasks, ordered by most recent first."""
        query = self.db.query(TranscriptionTask).order_by(
            TranscriptionTask.started_at.desc()
        )
        total = query.count()
        total_pages = max(1, math.ceil(total / page_size))
        items = query.offset((page - 1) * page_size).limit(page_size).all()

        return PaginatedResponse(
            items=[TranscriptionTaskResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
