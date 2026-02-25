"""API integration tests for audio and transcription routes."""

import io
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.audio_file import AudioFile
from app.models.database import Base, get_db
from app.models.transcription_task import TranscriptionTask
from app.main import app
from app.schemas.transcript import TranscriptResult, TranscriptSegment, WordSegment


# --- Test database setup ---

@pytest.fixture(autouse=True)
def _setup_upload_dir(tmp_path, monkeypatch):
    """Redirect upload directory to tmp_path for test isolation."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr("app.services.audio_manager.UPLOAD_DIR", upload_dir)


@pytest.fixture
def db_engine(tmp_path):
    url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest_asyncio.fixture
async def client(db_session):
    """Create an async test client with overridden DB dependency."""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _make_audio_bytes(size: int = 1024) -> bytes:
    """Generate dummy audio content."""
    return b"\x00" * size


# ============================================================
# Audio Upload Tests
# ============================================================

@pytest.mark.asyncio
async def test_upload_single_file(client):
    content = _make_audio_bytes()
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("test.wav", io.BytesIO(content), "audio/wav"))],
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data) == 1
    assert data[0]["filename"] == "test.wav"
    assert data[0]["format"] == "wav"
    assert data[0]["transcription_status"] == "pending"
    assert data[0]["file_size"] == len(content)


@pytest.mark.asyncio
async def test_upload_multiple_files(client):
    files = [
        ("files", (f"audio{i}.mp3", io.BytesIO(_make_audio_bytes()), "audio/mpeg"))
        for i in range(3)
    ]
    resp = await client.post("/api/audio/upload", files=files)
    assert resp.status_code == 201
    assert len(resp.json()) == 3


@pytest.mark.asyncio
async def test_upload_unsupported_format(client):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("doc.txt", io.BytesIO(b"hello"), "text/plain"))],
    )
    assert resp.status_code == 400
    assert "Unsupported format" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_all_supported_formats(client):
    for fmt in ("wav", "mp3", "flac", "m4a", "ogg"):
        resp = await client.post(
            "/api/audio/upload",
            files=[("files", (f"test.{fmt}", io.BytesIO(_make_audio_bytes()), "application/octet-stream"))],
        )
        assert resp.status_code == 201, f"Failed for format: {fmt}"


# ============================================================
# Audio Get / List Tests
# ============================================================

@pytest.mark.asyncio
async def test_get_audio_by_id(client):
    # Upload first
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("get_me.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    resp = await client.get(f"/api/audio/{audio_id}")
    assert resp.status_code == 200
    assert resp.json()["filename"] == "get_me.wav"


@pytest.mark.asyncio
async def test_get_audio_not_found(client):
    resp = await client.get("/api/audio/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_audio_pagination(client):
    # Upload 5 files
    for i in range(5):
        await client.post(
            "/api/audio/upload",
            files=[("files", (f"page_{i}.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
        )

    resp = await client.get("/api/audio", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total_pages"] == 3


@pytest.mark.asyncio
async def test_list_audio_search(client):
    await client.post(
        "/api/audio/upload",
        files=[("files", ("meeting_notes.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    await client.post(
        "/api/audio/upload",
        files=[("files", ("music_track.mp3", io.BytesIO(_make_audio_bytes()), "audio/mpeg"))],
    )

    resp = await client.get("/api/audio", params={"search": "meeting"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert "meeting" in items[0]["filename"].lower()


@pytest.mark.asyncio
async def test_list_audio_sort(client):
    await client.post(
        "/api/audio/upload",
        files=[("files", ("alpha.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    await client.post(
        "/api/audio/upload",
        files=[("files", ("beta.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )

    resp = await client.get("/api/audio", params={"sort_by": "filename", "sort_order": "asc"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["filename"] == "alpha.wav"
    assert items[1]["filename"] == "beta.wav"


# ============================================================
# Audio Replace Tests
# ============================================================

@pytest.mark.asyncio
async def test_replace_audio(client):
    # Upload original
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("original.wav", io.BytesIO(_make_audio_bytes(512)), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    # Replace
    new_content = _make_audio_bytes(2048)
    resp = await client.put(
        f"/api/audio/{audio_id}",
        files=[("file", ("replaced.mp3", io.BytesIO(new_content), "audio/mpeg"))],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "replaced.mp3"
    assert data["format"] == "mp3"
    assert data["file_size"] == len(new_content)
    assert data["id"] == audio_id


@pytest.mark.asyncio
async def test_replace_audio_not_found(client):
    resp = await client.put(
        "/api/audio/99999",
        files=[("file", ("new.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_replace_audio_unsupported_format(client):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("orig.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    resp = await client.put(
        f"/api/audio/{audio_id}",
        files=[("file", ("bad.txt", io.BytesIO(b"text"), "text/plain"))],
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_replace_audio_while_processing(client, db_session):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("proc.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    # Manually set status to processing
    record = db_session.query(AudioFile).filter_by(id=audio_id).first()
    record.transcription_status = "processing"
    db_session.commit()

    resp = await client.put(
        f"/api/audio/{audio_id}",
        files=[("file", ("new.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    assert resp.status_code == 409


# ============================================================
# Audio Delete Tests
# ============================================================

@pytest.mark.asyncio
async def test_delete_audio(client):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("delete_me.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    resp = await client.delete(f"/api/audio/{audio_id}")
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get(f"/api/audio/{audio_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_audio_while_processing(client, db_session):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("busy.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    record = db_session.query(AudioFile).filter_by(id=audio_id).first()
    record.transcription_status = "processing"
    db_session.commit()

    resp = await client.delete(f"/api/audio/{audio_id}")
    assert resp.status_code == 409


# ============================================================
# Transcript Tests
# ============================================================

@pytest.mark.asyncio
async def test_get_transcript_completed(client, db_session):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("done.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    # Simulate completed transcription
    transcript = TranscriptResult(
        segments=[
            TranscriptSegment(
                text="Hello world",
                start=0.0,
                end=1.5,
                words=[
                    WordSegment(word="Hello", start=0.0, end=0.7, score=0.95),
                    WordSegment(word="world", start=0.8, end=1.5, score=0.92),
                ],
            )
        ],
        language="en",
        duration=1.5,
    )
    record = db_session.query(AudioFile).filter_by(id=audio_id).first()
    record.transcription_status = "completed"
    record.transcript_json = transcript.to_json()
    db_session.commit()

    resp = await client.get(f"/api/audio/{audio_id}/transcript")
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "en"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["text"] == "Hello world"


@pytest.mark.asyncio
async def test_get_transcript_not_transcribed(client):
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("pending.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )
    audio_id = resp.json()[0]["id"]

    resp = await client.get(f"/api/audio/{audio_id}/transcript")
    assert resp.status_code == 404
    assert "not available" in resp.json()["detail"].lower() or "not been transcribed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_transcript_file_not_found(client):
    resp = await client.get("/api/audio/99999/transcript")
    assert resp.status_code == 404


# ============================================================
# Transcription Trigger Tests
# ============================================================

@pytest.mark.asyncio
async def test_trigger_transcription_all_pending(client, db_session):
    """Trigger transcription for all pending files (mocked engine)."""
    # Upload files
    for i in range(2):
        await client.post(
            "/api/audio/upload",
            files=[("files", (f"t{i}.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
        )

    mock_result = TranscriptResult(
        segments=[
            TranscriptSegment(
                text="test",
                start=0.0,
                end=1.0,
                words=[WordSegment(word="test", start=0.0, end=1.0, score=0.9)],
            )
        ],
        language="en",
        duration=1.0,
    )

    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.transcribe_with_retry.return_value = mock_result
        MockEngine.return_value = mock_engine

        resp = await client.post("/api/transcription/trigger")
        assert resp.status_code == 201
        data = resp.json()
        assert data["trigger_type"] == "manual"
        assert data["total_files"] == 2
        assert data["success_count"] == 2
        assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_trigger_transcription_specific_files(client, db_session):
    """Trigger transcription for specific file IDs only."""
    ids = []
    for i in range(3):
        resp = await client.post(
            "/api/audio/upload",
            files=[("files", (f"sel{i}.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
        )
        ids.append(resp.json()[0]["id"])

    mock_result = TranscriptResult(
        segments=[
            TranscriptSegment(
                text="selected",
                start=0.0,
                end=1.0,
                words=[WordSegment(word="selected", start=0.0, end=1.0, score=0.9)],
            )
        ],
        language="en",
        duration=1.0,
    )

    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.transcribe_with_retry.return_value = mock_result
        MockEngine.return_value = mock_engine

        resp = await client.post(
            "/api/transcription/trigger",
            json={"file_ids": [ids[0], ids[2]]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["total_files"] == 2


@pytest.mark.asyncio
async def test_trigger_transcription_mutex(client, db_session):
    """Second trigger should fail with 409 when a task is already running."""
    from datetime import datetime, timezone

    # Create a running task directly in DB
    task = TranscriptionTask(
        trigger_type="manual",
        status="running",
        total_files=1,
        processed_files=0,
        success_count=0,
        failure_count=0,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    db_session.commit()

    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        MockEngine.return_value = MagicMock()

        resp = await client.post("/api/transcription/trigger")
        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"].lower()


# ============================================================
# Transcription Task List / Get Tests
# ============================================================

@pytest.mark.asyncio
async def test_list_tasks_empty(client):
    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        MockEngine.return_value = MagicMock()

        resp = await client.get("/api/transcription/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_get_task_not_found(client):
    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        MockEngine.return_value = MagicMock()

        resp = await client.get("/api/transcription/tasks/99999")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_tasks_after_trigger(client):
    # Upload a file
    await client.post(
        "/api/audio/upload",
        files=[("files", ("task_list.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )

    mock_result = TranscriptResult(
        segments=[
            TranscriptSegment(
                text="hi",
                start=0.0,
                end=0.5,
                words=[WordSegment(word="hi", start=0.0, end=0.5, score=0.9)],
            )
        ],
        language="en",
        duration=0.5,
    )

    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.transcribe_with_retry.return_value = mock_result
        MockEngine.return_value = mock_engine

        await client.post("/api/transcription/trigger")

        resp = await client.get("/api/transcription/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_get_task_by_id(client):
    await client.post(
        "/api/audio/upload",
        files=[("files", ("task_get.wav", io.BytesIO(_make_audio_bytes()), "audio/wav"))],
    )

    mock_result = TranscriptResult(
        segments=[
            TranscriptSegment(
                text="ok",
                start=0.0,
                end=0.3,
                words=[WordSegment(word="ok", start=0.0, end=0.3, score=0.9)],
            )
        ],
        language="en",
        duration=0.3,
    )

    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.transcribe_with_retry.return_value = mock_result
        MockEngine.return_value = mock_engine

        trigger_resp = await client.post("/api/transcription/trigger")
        task_id = trigger_resp.json()["id"]

        resp = await client.get(f"/api/transcription/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id


# ============================================================
# Full Workflow: Upload → Trigger → Check Transcript
# ============================================================

@pytest.mark.asyncio
async def test_full_workflow(client, db_session):
    """End-to-end: upload → trigger transcription → verify transcript available."""
    # 1. Upload
    resp = await client.post(
        "/api/audio/upload",
        files=[("files", ("workflow.flac", io.BytesIO(_make_audio_bytes()), "audio/flac"))],
    )
    assert resp.status_code == 201
    audio_id = resp.json()[0]["id"]

    # 2. Trigger transcription
    mock_result = TranscriptResult(
        segments=[
            TranscriptSegment(
                text="Full workflow test",
                start=0.0,
                end=2.0,
                speaker="SPEAKER_00",
                words=[
                    WordSegment(word="Full", start=0.0, end=0.5, score=0.95, speaker="SPEAKER_00"),
                    WordSegment(word="workflow", start=0.6, end=1.2, score=0.93, speaker="SPEAKER_00"),
                    WordSegment(word="test", start=1.3, end=2.0, score=0.91, speaker="SPEAKER_00"),
                ],
            )
        ],
        language="en",
        duration=2.0,
    )

    with patch("app.routers.transcription.TranscriptionEngine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.transcribe_with_retry.return_value = mock_result
        MockEngine.return_value = mock_engine

        resp = await client.post("/api/transcription/trigger", json={"file_ids": [audio_id]})
        assert resp.status_code == 201
        assert resp.json()["success_count"] == 1

    # 3. Verify transcript
    resp = await client.get(f"/api/audio/{audio_id}/transcript")
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "en"
    assert data["segments"][0]["text"] == "Full workflow test"
    assert data["segments"][0]["speaker"] == "SPEAKER_00"

    # 4. Verify audio status updated
    resp = await client.get(f"/api/audio/{audio_id}")
    assert resp.status_code == 200
    assert resp.json()["transcription_status"] == "completed"
