from __future__ import annotations
"""Pipeline that turns uploaded media into multi-language subtitle files.

Two stages, both state-driven off the DB (see docs/subtitle-platform-design.md §6):
  * process_media: S3 video -> ffmpeg audio -> WhisperX transcribe+align ->
                   store transcript + source-language SubtitleTrack (SRT/VTT in S3).
  * process_track: translate the source transcript into one target language ->
                   SRT/VTT in S3 (reusing source timestamps).

The pipeline is deliberately dependency-injected (storage, transcription engine,
translation engine) so it is unit-testable with fakes and contains no global state.
The Worker (worker.py) drives it in a loop.
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.media_file import MediaFile
from app.models.subtitle_track import SubtitleTrack
from app.services import storage as storage_keys
from app.services.subtitles import SubtitleCue, chunk_into_cues, to_srt, to_vtt

logger = logging.getLogger(__name__)


def _segments_to_dicts(transcript_result) -> list[dict]:
    """Normalize a TranscriptResult (or dict) into [{start,end,text,words}].

    Word-level timings are preserved so the subtitle layer can re-chunk coarse
    segments into short, synced cues.
    """
    segments = getattr(transcript_result, "segments", None)
    if segments is None and isinstance(transcript_result, dict):
        segments = transcript_result.get("segments", [])
    out = []
    for seg in segments or []:
        if isinstance(seg, dict):
            words = [
                {"word": w.get("word", ""), "start": w.get("start"), "end": w.get("end")}
                for w in (seg.get("words") or [])
            ]
            out.append({"start": seg.get("start", 0.0), "end": seg.get("end", 0.0),
                        "text": seg.get("text", ""), "words": words})
        else:
            words = [
                {"word": w.word, "start": w.start, "end": w.end}
                for w in (getattr(seg, "words", None) or [])
            ]
            out.append({"start": seg.start, "end": seg.end, "text": seg.text,
                        "words": words})
    return out


@dataclass
class SubtitlePipeline:
    db: Session
    storage: object              # StorageService
    transcription_engine: object  # TranscriptionEngine (lazy GPU/CPU model)
    translation_engine: object    # TranslationEngine (Bedrock)

    # --- stage 1: transcription ---------------------------------------------

    def process_media(self, media_id: int) -> None:
        media = self.db.query(MediaFile).filter_by(id=media_id).first()
        if media is None:
            logger.warning("process_media: media %s gone", media_id)
            return

        media.transcription_status = "processing"
        media.error_message = None
        self.db.commit()

        tmpdir = tempfile.mkdtemp(prefix=f"media_{media_id}_")
        try:
            video_path = os.path.join(tmpdir, "source")
            self.storage.download_file(media.s3_key, video_path)

            # Extract a 16 kHz mono wav (works for both video and audio inputs).
            from app.services.subtitles import extract_audio
            audio_path = os.path.join(tmpdir, "audio.wav")
            extract_audio(video_path, audio_path)

            result = self.transcription_engine.transcribe_with_retry(audio_path)
            segments = _segments_to_dicts(result)
            language = getattr(result, "language", None) or "unknown"

            media.transcript_json = _result_to_json(result)
            media.source_language = language

            # Re-chunk coarse segments into short, word-timed cues (movie-style).
            cues = chunk_into_cues(segments)

            # Create/refresh the source-language track and write its files.
            self._write_subtitle_files(
                media_id=media.id,
                language=language,
                cues=cues,
                is_source=True,
            )
            media.transcription_status = "completed"
            self.db.commit()
            logger.info(
                "media %s transcribed (%s, %d segments -> %d cues)",
                media_id, language, len(segments), len(cues),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("transcription failed for media %s", media_id)
            self.db.rollback()
            media = self.db.query(MediaFile).filter_by(id=media_id).first()
            if media is not None:
                media.transcription_status = "failed"
                media.error_message = str(exc)
                self.db.commit()
        finally:
            _rmtree(tmpdir)

    # --- stage 2: translation -----------------------------------------------

    def process_track(self, track_id: int) -> None:
        track = self.db.query(SubtitleTrack).filter_by(id=track_id).first()
        if track is None or track.is_source:
            return
        media = self.db.query(MediaFile).filter_by(id=track.media_file_id).first()
        if media is None or not media.transcript_json:
            track.status = "failed"
            track.error_message = "source transcript not available"
            self.db.commit()
            return

        track.status = "processing"
        track.error_message = None
        self.db.commit()

        try:
            segments = _segments_to_dicts(json.loads(media.transcript_json))
            # Chunk into short cues FIRST, then translate those cues — so the
            # translated subtitles are just as short and share the cue timings.
            source_cues = chunk_into_cues(segments)
            cue_segments = [
                {"start": c.start, "end": c.end, "text": c.text} for c in source_cues
            ]
            translated = self.translation_engine.translate_segments(
                cue_segments, target_language=track.language,
                source_language=media.source_language,
            )
            translated_cues = [
                SubtitleCue(start=t["start"], end=t["end"], text=t["text"])
                for t in translated
            ]
            self._write_subtitle_files(
                media_id=media.id,
                language=track.language,
                cues=translated_cues,
                is_source=False,
                track=track,
            )
            track.status = "completed"
            self.db.commit()
            logger.info(
                "media %s translated -> %s (%d cues)",
                media.id, track.language, len(translated_cues),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("translation failed for track %s", track_id)
            self.db.rollback()
            track = self.db.query(SubtitleTrack).filter_by(id=track_id).first()
            if track is not None:
                track.status = "failed"
                track.error_message = str(exc)
                self.db.commit()

    # --- shared --------------------------------------------------------------

    def _write_subtitle_files(
        self, media_id: int, language: str, cues: list,
        is_source: bool, track: SubtitleTrack | None = None,
    ) -> SubtitleTrack:
        """Render SRT+VTT from cues, upload to S3, and upsert the SubtitleTrack row."""
        srt_key = storage_keys.subtitle_key(media_id, language, "srt")
        vtt_key = storage_keys.subtitle_key(media_id, language, "vtt")
        self.storage.put_bytes(srt_key, to_srt(cues).encode("utf-8"), "text/plain; charset=utf-8")
        self.storage.put_bytes(vtt_key, to_vtt(cues).encode("utf-8"), "text/vtt; charset=utf-8")

        if track is None:
            track = (
                self.db.query(SubtitleTrack)
                .filter_by(media_file_id=media_id, language=language)
                .first()
            )
        if track is None:
            track = SubtitleTrack(media_file_id=media_id, language=language)
            self.db.add(track)
        track.is_source = is_source
        track.srt_s3_key = srt_key
        track.vtt_s3_key = vtt_key
        track.status = "completed"
        track.error_message = None
        return track


def _result_to_json(result) -> str:
    """Serialize a TranscriptResult (pydantic) or dict to JSON."""
    if hasattr(result, "to_json"):
        return result.to_json()
    return json.dumps(result)


def _rmtree(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
