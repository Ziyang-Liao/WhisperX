from __future__ import annotations
"""Background worker that drives the subtitle pipeline off the DB state queue.

A single daemon thread polls for pending work and processes it serially:
  1. media with transcription_status == 'pending'  -> transcribe
  2. subtitle_tracks with status == 'pending' (non-source, media completed) -> translate

Serial processing is intentional: one CPU box transcribes one file at a time,
and the WhisperX model is loaded once (held by the injected engine), not per task.
For higher throughput, swap this loop for an SQS consumer (design §11) — the
SubtitlePipeline itself is unchanged.
"""

import logging
import threading
import time

from app.models.database import SessionLocal
from app.models.media_file import MediaFile
from app.models.subtitle_track import SubtitleTrack
from app.services.subtitle_pipeline import SubtitlePipeline

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5.0  # seconds


class SubtitleWorker:
    def __init__(self, transcription_engine, translation_engine, storage, poll_interval: float = POLL_INTERVAL):
        self.transcription_engine = transcription_engine
        self.translation_engine = translation_engine
        self.storage = storage
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="subtitle-worker", daemon=True)
        self._thread.start()
        logger.info("subtitle worker started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("subtitle worker stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = self._process_one()
            except Exception:  # noqa: BLE001 - never let the loop die
                logger.exception("worker iteration failed")
                did_work = False
            # If we did work, immediately look for more; otherwise back off.
            if not did_work:
                self._stop.wait(self.poll_interval)

    def _process_one(self) -> bool:
        """Claim and process a single unit of work. Returns True if work was done."""
        db = SessionLocal()
        try:
            pipeline = SubtitlePipeline(
                db=db,
                storage=self.storage,
                transcription_engine=self.transcription_engine,
                translation_engine=self.translation_engine,
            )

            # Stage 1 takes priority: a media must be transcribed before its
            # non-source tracks can translate.
            media = (
                db.query(MediaFile)
                .filter(MediaFile.transcription_status == "pending")
                .order_by(MediaFile.id.asc())
                .first()
            )
            if media is not None:
                pipeline.process_media(media.id)
                return True

            track = (
                db.query(SubtitleTrack)
                .join(MediaFile, SubtitleTrack.media_file_id == MediaFile.id)
                .filter(
                    SubtitleTrack.status == "pending",
                    SubtitleTrack.is_source.is_(False),
                    MediaFile.transcription_status == "completed",
                )
                .order_by(SubtitleTrack.id.asc())
                .first()
            )
            if track is not None:
                pipeline.process_track(track.id)
                return True

            return False
        finally:
            db.close()
