"""API tests for the media router with a fake StorageService (no AWS)."""

import io

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.database import Base, get_db
from app.models.media_file import MediaFile
from app.routers import media as media_router


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def presigned_put(self, key, content_type=None, ttl=900):
        return f"https://s3.test/{key}?signed=put"

    def presigned_get(self, key, ttl=900):
        return f"https://s3.test/{key}?signed=get"

    def head(self, key):
        # Pretend the client uploaded successfully.
        return {"ContentLength": 12345}

    def delete_prefixes(self, prefixes):
        return 0


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/m.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest_asyncio.fixture
async def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    media_router.set_storage(FakeStorage())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    media_router.set_storage(None)


@pytest.mark.asyncio
async def test_create_media_returns_presigned_url(client):
    resp = await client.post("/api/media", json={"filename": "clip.mp4", "content_type": "video/mp4"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["media_id"] > 0
    assert "signed=put" in data["upload_url"]
    assert data["s3_key"] == f"videos/{data['media_id']}/source.mp4"


@pytest.mark.asyncio
async def test_create_media_rejects_unsupported_format(client):
    resp = await client.post("/api/media", json={"filename": "doc.pdf"})
    assert resp.status_code == 400
    assert "Unsupported format" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_complete_upload_enqueues(client):
    create = await client.post("/api/media", json={"filename": "v.mp4"})
    mid = create.json()["media_id"]
    resp = await client.post(f"/api/media/{mid}/complete")
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcription_status"] == "pending"
    assert body["file_size"] == 12345


@pytest.mark.asyncio
async def test_list_hides_created_but_shows_pending(client):
    # One created-but-not-completed (hidden), one completed-upload (shown).
    await client.post("/api/media", json={"filename": "hidden.mp4"})
    create = await client.post("/api/media", json={"filename": "shown.mp4"})
    await client.post(f"/api/media/{create.json()['media_id']}/complete")

    resp = await client.get("/api/media")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["filename"] == "shown.mp4"


@pytest.mark.asyncio
async def test_delete_allowed_while_processing(client, db_session):
    """Delete must work even when transcription is 'processing' (acts as cancel).

    Regression: it previously returned 409, which left a stuck job permanently
    undeletable.
    """
    create = await client.post("/api/media", json={"filename": "stuck.mp4"})
    mid = create.json()["media_id"]
    await client.post(f"/api/media/{mid}/complete")
    rec = db_session.query(MediaFile).filter_by(id=mid).first()
    rec.transcription_status = "processing"
    db_session.commit()

    resp = await client.delete(f"/api/media/{mid}")
    assert resp.status_code == 204
    assert db_session.query(MediaFile).filter_by(id=mid).first() is None


@pytest.mark.asyncio
async def test_generate_subtitles_skips_source_language(client, db_session):
    create = await client.post("/api/media", json={"filename": "v.mp4"})
    mid = create.json()["media_id"]
    await client.post(f"/api/media/{mid}/complete")
    # Simulate transcription finished with source language 'en'.
    rec = db_session.query(MediaFile).filter_by(id=mid).first()
    rec.source_language = "en"
    rec.transcription_status = "completed"
    db_session.commit()

    resp = await client.post(f"/api/media/{mid}/subtitles", json={"target_languages": ["en", "ja", "fr"]})
    assert resp.status_code == 201
    langs = sorted(t["language"] for t in resp.json())
    assert langs == ["fr", "ja"]  # 'en' skipped (== source)
    assert all(t["status"] == "pending" for t in resp.json())


@pytest.mark.asyncio
async def test_generate_subtitles_idempotent(client, db_session):
    create = await client.post("/api/media", json={"filename": "v.mp4"})
    mid = create.json()["media_id"]
    await client.post(f"/api/media/{mid}/complete")

    await client.post(f"/api/media/{mid}/subtitles", json={"target_languages": ["ja"]})
    await client.post(f"/api/media/{mid}/subtitles", json={"target_languages": ["ja"]})
    resp = await client.get(f"/api/media/{mid}/subtitles")
    ja = [t for t in resp.json() if t["language"] == "ja"]
    assert len(ja) == 1  # not duplicated
