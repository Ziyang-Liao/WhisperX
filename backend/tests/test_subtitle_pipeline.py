"""End-to-end pipeline tests with fakes (no AWS, no ML model, no ffmpeg).

Verifies: transcription produces a source track + transcript; translation
produces a target track reusing source timestamps; both write SRT+VTT to the
(fake) storage; failures mark status='failed' with an error message.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base
from app.models.media_file import MediaFile
from app.models.subtitle_track import SubtitleTrack
from app.services.subtitle_pipeline import SubtitlePipeline


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/p.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def download_file(self, key, local_path):
        with open(local_path, "wb") as f:
            f.write(b"fake-video-bytes")
        return local_path

    def put_bytes(self, key, data, content_type=None):
        self.objects[key] = data
        return key


class FakeTranscriptResult:
    language = "en"
    segments = [
        {"start": 0.0, "end": 1.5, "text": "hello"},
        {"start": 1.5, "end": 3.0, "text": "world"},
    ]

    def to_json(self):
        return json.dumps({"language": self.language, "segments": self.segments,
                           "duration": 3.0})


class FakeTranscriptionEngine:
    def transcribe_with_retry(self, audio_path):
        return FakeTranscriptResult()


class FakeTranslationEngine:
    def translate_segments(self, segments, target_language, source_language=None):
        return [{"start": s["start"], "end": s["end"], "text": f"[{target_language}]{s['text']}"}
                for s in segments]


def _pipeline(db, monkeypatch, translation=None):
    # Skip real ffmpeg — extract_audio is imported inside process_media.
    import app.services.subtitles as subs
    monkeypatch.setattr(subs, "extract_audio", lambda v, a: a)
    return SubtitlePipeline(
        db=db,
        storage=FakeStorage(),
        transcription_engine=FakeTranscriptionEngine(),
        translation_engine=translation or FakeTranslationEngine(),
    )


def test_process_media_creates_source_track_and_files(db, monkeypatch):
    media = MediaFile(filename="v.mp4", media_type="video", s3_key="videos/1/source.mp4", format="mp4")
    db.add(media)
    db.commit()

    pipe = _pipeline(db, monkeypatch)
    pipe.process_media(media.id)

    db.refresh(media)
    assert media.transcription_status == "completed"
    assert media.source_language == "en"
    tracks = db.query(SubtitleTrack).filter_by(media_file_id=media.id).all()
    assert len(tracks) == 1
    assert tracks[0].is_source and tracks[0].language == "en"
    # SRT + VTT both written
    assert "subtitles/1/en.srt" in pipe.storage.objects
    assert "subtitles/1/en.vtt" in pipe.storage.objects
    assert pipe.storage.objects["subtitles/1/en.srt"].decode().startswith("1\n00:00:00,000")


def test_process_track_translates_and_preserves_timing(db, monkeypatch):
    media = MediaFile(filename="v.mp4", media_type="video", s3_key="videos/1/source.mp4", format="mp4")
    db.add(media)
    db.commit()
    pipe = _pipeline(db, monkeypatch)
    pipe.process_media(media.id)

    # Request a Japanese track.
    track = SubtitleTrack(media_file_id=media.id, language="ja", is_source=False, status="pending")
    db.add(track)
    db.commit()

    pipe.process_track(track.id)
    db.refresh(track)
    assert track.status == "completed"
    srt = pipe.storage.objects["subtitles/1/ja.srt"].decode()
    assert "[ja]hello" in srt and "[ja]world" in srt
    # Same timing as source
    assert "00:00:00,000 --> 00:00:01,500" in srt


def test_translation_failure_marks_track_failed(db, monkeypatch):
    media = MediaFile(filename="v.mp4", media_type="video", s3_key="videos/1/source.mp4", format="mp4")
    db.add(media)
    db.commit()
    pipe = _pipeline(db, monkeypatch)
    pipe.process_media(media.id)

    class Boom:
        def translate_segments(self, *a, **k):
            raise RuntimeError("bedrock down")

    pipe.translation_engine = Boom()
    track = SubtitleTrack(media_file_id=media.id, language="fr", is_source=False, status="pending")
    db.add(track)
    db.commit()

    pipe.process_track(track.id)
    db.refresh(track)
    assert track.status == "failed"
    assert "bedrock down" in (track.error_message or "")
