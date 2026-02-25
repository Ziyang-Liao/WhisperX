# Feature: batch-speech-to-text, Property 2: 音频格式验证
# Feature: batch-speech-to-text, Property 1: 上传创建完整元数据记录
# Feature: batch-speech-to-text, Property 3: 替换文件更新元数据
# Feature: batch-speech-to-text, Property 4: 删除文件级联清理
# Feature: batch-speech-to-text, Property 5: 转录中文件不可修改
# Feature: batch-speech-to-text, Property 11: 分页正确性
# Feature: batch-speech-to-text, Property 12: 搜索结果匹配性
# Feature: batch-speech-to-text, Property 13: 排序正确性

import asyncio
import io
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.audio_file import AudioFile
from app.models.database import Base
from app.models.task_file_record import TaskFileRecord
from app.models.transcription_task import TranscriptionTask
from app.services.audio_manager import AudioManager, SUPPORTED_FORMATS


# --- Test fixtures ---

@pytest.fixture
def db_session(tmp_path):
    """Create a fresh in-memory SQLite database for each test."""
    db_url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


# --- Strategies ---

# Strategy for generating supported file extensions
supported_ext_strategy = st.sampled_from(sorted(SUPPORTED_FORMATS))

# Strategy for generating unsupported file extensions
unsupported_ext_strategy = st.text(
    min_size=1, max_size=10,
    alphabet=st.characters(categories=("L", "N"))
).filter(lambda x: x.lower() not in SUPPORTED_FORMATS)

# Strategy for generating valid filenames with supported extensions
valid_filename_strategy = st.builds(
    lambda name, ext: f"{name}.{ext}",
    name=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N"))),
    ext=supported_ext_strategy,
)


# --- Property 2: 音频格式验证 ---

@settings(max_examples=100)
@given(ext=supported_ext_strategy)
def test_property_2_supported_format_accepted(ext: str):
    """Property 2: Supported audio formats should pass validation."""
    filename = f"test_audio.{ext}"
    assert AudioManager.validate_format(filename) is True


@settings(max_examples=100)
@given(ext=unsupported_ext_strategy)
def test_property_2_unsupported_format_rejected(ext: str):
    """Property 2: Unsupported audio formats should be rejected."""
    filename = f"test_audio.{ext}"
    assert AudioManager.validate_format(filename) is False


def test_property_2_no_extension_rejected():
    """Property 2: Files without extensions should be rejected."""
    assert AudioManager.validate_format("noextension") is False
    assert AudioManager.validate_format("") is False


@settings(max_examples=100)
@given(ext=supported_ext_strategy)
def test_property_2_case_insensitive(ext: str):
    """Property 2: Format validation should be case-insensitive."""
    assert AudioManager.validate_format(f"file.{ext.upper()}") is True
    assert AudioManager.validate_format(f"file.{ext.lower()}") is True


# --- Helpers ---

def _make_upload_file(filename: str, content: bytes) -> MagicMock:
    """Create a mock UploadFile."""
    mock = MagicMock(spec=["filename", "read"])
    mock.filename = filename
    mock.read = AsyncMock(return_value=content)
    return mock


def _make_db_session(tmp_dir: str):
    """Create a fresh SQLite DB session in the given directory."""
    db_url = f"sqlite:///{tmp_dir}/test.db"
    eng = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    Sess = sessionmaker(bind=eng)
    return Sess(), eng


# --- Property 1: 上传创建完整元数据记录 ---

@settings(max_examples=100)
@given(
    name=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
    ext=supported_ext_strategy,
    content_size=st.integers(min_value=100, max_value=10000),
)
def test_property_1_upload_creates_complete_metadata(name, ext, content_size):
    """Property 1: Upload should create a record with complete and consistent metadata."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            filename = f"{name}.{ext}"
            content = os.urandom(content_size)
            mock_file = _make_upload_file(filename, content)
            upload_dir = Path(tmp_dir) / "uploads"

            manager = AudioManager(session)

            mock_duration = AsyncMock(return_value=1.5)
            with patch.object(AudioManager, '_extract_duration', mock_duration):
                with patch("app.services.audio_manager.UPLOAD_DIR", upload_dir):
                    loop = asyncio.new_event_loop()
                    try:
                        records = loop.run_until_complete(
                            manager.upload_files([mock_file])
                        )
                    finally:
                        loop.close()

            assert len(records) == 1
            record = records[0]

            # Verify complete metadata
            assert record.filename == filename
            assert record.file_size == content_size
            assert record.file_size > 0
            assert record.duration > 0
            assert record.format == ext.lower()
            assert record.upload_time is not None
            assert record.transcription_status == "pending"
            assert record.id is not None

            # Verify record persisted in DB
            db_record = session.query(AudioFile).filter_by(id=record.id).first()
            assert db_record is not None
            assert db_record.filename == filename
            assert db_record.file_size == content_size
        finally:
            session.close()
            engine.dispose()


# --- Property 3: 替换文件更新元数据 ---

@settings(max_examples=100)
@given(
    orig_name=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    orig_ext=supported_ext_strategy,
    new_name=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    new_ext=supported_ext_strategy,
    orig_size=st.integers(min_value=100, max_value=5000),
    new_size=st.integers(min_value=100, max_value=5000),
)
def test_property_3_replace_updates_metadata(orig_name, orig_ext, new_name, new_ext, orig_size, new_size):
    """Property 3: After replacement, metadata matches the new file and ID is unchanged."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            upload_dir = Path(tmp_dir) / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            # Create original record
            orig_filename = f"{orig_name}.{orig_ext}"
            orig_stored = upload_dir / f"orig.{orig_ext}"
            orig_stored.write_bytes(os.urandom(orig_size))

            record = AudioFile(
                filename=orig_filename,
                stored_path=str(orig_stored),
                file_size=orig_size,
                duration=1.0,
                format=orig_ext,
                transcription_status="pending",
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            original_id = record.id

            # Replace with new file
            new_filename = f"{new_name}.{new_ext}"
            new_content = os.urandom(new_size)
            mock_file = _make_upload_file(new_filename, new_content)

            manager = AudioManager(session)
            mock_duration = AsyncMock(return_value=2.5)
            with patch.object(AudioManager, '_extract_duration', mock_duration):
                with patch("app.services.audio_manager.UPLOAD_DIR", upload_dir):
                    loop = asyncio.new_event_loop()
                    try:
                        updated = loop.run_until_complete(
                            manager.replace_file(original_id, mock_file)
                        )
                    finally:
                        loop.close()

            # ID unchanged
            assert updated.id == original_id
            # Metadata matches new file
            assert updated.filename == new_filename
            assert updated.file_size == new_size
            assert updated.format == new_ext.lower()
            assert updated.duration == 2.5
            assert updated.transcription_status == "pending"
        finally:
            session.close()
            engine.dispose()


# --- Property 4: 删除文件级联清理 ---

@settings(max_examples=100)
@given(
    num_files=st.integers(min_value=1, max_value=5),
)
def test_property_4_delete_cascades_cleanup(num_files):
    """Property 4: Deleting files removes DB records, task records, and physical files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            upload_dir = Path(tmp_dir) / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            audio_ids = []
            stored_paths = []

            for i in range(num_files):
                stored = upload_dir / f"file_{i}.wav"
                stored.write_bytes(os.urandom(100))
                stored_paths.append(stored)

                record = AudioFile(
                    filename=f"file_{i}.wav",
                    stored_path=str(stored),
                    file_size=100,
                    duration=1.0,
                    format="wav",
                    transcription_status="completed",
                    transcript_json='{"segments":[],"language":"en","duration":1.0}',
                )
                session.add(record)
                session.commit()
                session.refresh(record)
                audio_ids.append(record.id)

            # Create a task with file records
            task = TranscriptionTask(
                trigger_type="manual",
                status="completed",
                total_files=num_files,
                processed_files=num_files,
                success_count=num_files,
                failure_count=0,
            )
            session.add(task)
            session.commit()
            session.refresh(task)

            for aid in audio_ids:
                tfr = TaskFileRecord(
                    task_id=task.id,
                    audio_file_id=aid,
                    status="completed",
                )
                session.add(tfr)
            session.commit()

            # Delete all files
            manager = AudioManager(session)
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(manager.delete_files(audio_ids))
            finally:
                loop.close()

            # Verify: no audio records remain
            remaining = session.query(AudioFile).filter(AudioFile.id.in_(audio_ids)).all()
            assert len(remaining) == 0

            # Verify: no task file records remain for deleted audio
            remaining_tfr = session.query(TaskFileRecord).filter(
                TaskFileRecord.audio_file_id.in_(audio_ids)
            ).all()
            assert len(remaining_tfr) == 0

            # Verify: physical files removed
            for p in stored_paths:
                assert not p.exists()
        finally:
            session.close()
            engine.dispose()


# --- Property 5: 转录中文件不可修改 ---

@settings(max_examples=100)
@given(
    name=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    ext=supported_ext_strategy,
)
def test_property_5_processing_file_cannot_be_replaced(name, ext):
    """Property 5: Files with 'processing' status should reject replace operations."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            upload_dir = Path(tmp_dir) / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            stored = upload_dir / f"orig.{ext}"
            stored.write_bytes(os.urandom(100))

            record = AudioFile(
                filename=f"{name}.{ext}",
                stored_path=str(stored),
                file_size=100,
                duration=1.0,
                format=ext,
                transcription_status="processing",
            )
            session.add(record)
            session.commit()
            session.refresh(record)

            mock_file = _make_upload_file(f"new.{ext}", os.urandom(50))
            manager = AudioManager(session)

            loop = asyncio.new_event_loop()
            try:
                with pytest.raises(PermissionError):
                    loop.run_until_complete(
                        manager.replace_file(record.id, mock_file)
                    )
            finally:
                loop.close()
        finally:
            session.close()
            engine.dispose()


@settings(max_examples=100)
@given(
    name=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    ext=supported_ext_strategy,
)
def test_property_5_processing_file_cannot_be_deleted(name, ext):
    """Property 5: Files with 'processing' status should reject delete operations."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            upload_dir = Path(tmp_dir) / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            stored = upload_dir / f"orig.{ext}"
            stored.write_bytes(os.urandom(100))

            record = AudioFile(
                filename=f"{name}.{ext}",
                stored_path=str(stored),
                file_size=100,
                duration=1.0,
                format=ext,
                transcription_status="processing",
            )
            session.add(record)
            session.commit()
            session.refresh(record)

            manager = AudioManager(session)

            loop = asyncio.new_event_loop()
            try:
                with pytest.raises(PermissionError):
                    loop.run_until_complete(
                        manager.delete_files([record.id])
                    )
            finally:
                loop.close()
        finally:
            session.close()
            engine.dispose()


# --- Property 11: 分页正确性 ---

@settings(max_examples=100)
@given(
    num_records=st.integers(min_value=0, max_value=50),
    page_size=st.integers(min_value=1, max_value=20),
)
def test_property_11_pagination_correctness(num_records, page_size):
    """Property 11: Returned record count = min(page_size, N - (page-1)*page_size)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            # Seed records
            for i in range(num_records):
                record = AudioFile(
                    filename=f"file_{i}.wav",
                    stored_path=f"/tmp/file_{i}.wav",
                    file_size=100,
                    duration=1.0,
                    format="wav",
                    transcription_status="pending",
                )
                session.add(record)
            session.commit()

            total_pages = max(1, math.ceil(num_records / page_size))

            manager = AudioManager(session)

            for page in range(1, total_pages + 1):
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        manager.list_audio(page=page, page_size=page_size)
                    )
                finally:
                    loop.close()

                expected_count = min(page_size, num_records - (page - 1) * page_size)
                assert len(result.items) == expected_count
                assert result.total == num_records
                assert result.page == page
                assert result.page_size == page_size
        finally:
            session.close()
            engine.dispose()


# --- Property 12: 搜索结果匹配性 ---

@settings(max_examples=100)
@given(
    keyword=st.text(min_size=1, max_size=10, alphabet=st.characters(categories=("L",))),
    num_matching=st.integers(min_value=1, max_value=5),
    num_non_matching=st.integers(min_value=0, max_value=5),
)
def test_property_12_search_results_match(keyword, num_matching, num_non_matching):
    """Property 12: Every returned result's filename or transcript contains the search keyword."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            # Create matching records (keyword in filename)
            for i in range(num_matching):
                record = AudioFile(
                    filename=f"audio_{keyword}_{i}.wav",
                    stored_path=f"/tmp/match_{i}.wav",
                    file_size=100,
                    duration=1.0,
                    format="wav",
                    transcription_status="pending",
                )
                session.add(record)

            # Create non-matching records
            for i in range(num_non_matching):
                record = AudioFile(
                    filename=f"zzzzz_{i}.wav",
                    stored_path=f"/tmp/nomatch_{i}.wav",
                    file_size=100,
                    duration=1.0,
                    format="wav",
                    transcription_status="pending",
                )
                session.add(record)
            session.commit()

            manager = AudioManager(session)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    manager.list_audio(page=1, page_size=100, search=keyword)
                )
            finally:
                loop.close()

            # Every result should contain the keyword in filename (case-insensitive)
            for item in result.items:
                assert keyword.lower() in item.filename.lower() or (
                    hasattr(item, 'transcript_json') and item.transcript_json and
                    keyword.lower() in item.transcript_json.lower()
                ), f"Result '{item.filename}' does not contain keyword '{keyword}'"

            # At least the matching records should be returned
            assert result.total >= num_matching
        finally:
            session.close()
            engine.dispose()


# --- Property 13: 排序正确性 ---

@settings(max_examples=100)
@given(
    num_records=st.integers(min_value=2, max_value=15),
    sort_by=st.sampled_from(["upload_time", "filename", "transcription_status", "duration"]),
    sort_order=st.sampled_from(["asc", "desc"]),
)
def test_property_13_sort_correctness(num_records, sort_by, sort_order):
    """Property 13: Returned list is correctly sorted by the specified field."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine = _make_db_session(tmp_dir)
        try:
            import random
            statuses = ["pending", "processing", "completed", "failed"]

            for i in range(num_records):
                record = AudioFile(
                    filename=f"file_{random.randint(0, 9999):04d}.wav",
                    stored_path=f"/tmp/sort_{i}.wav",
                    file_size=random.randint(100, 10000),
                    duration=random.uniform(0.1, 100.0),
                    format="wav",
                    transcription_status=random.choice(statuses),
                )
                session.add(record)
            session.commit()

            manager = AudioManager(session)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    manager.list_audio(
                        page=1, page_size=num_records,
                        sort_by=sort_by, sort_order=sort_order,
                    )
                )
            finally:
                loop.close()

            items = result.items
            if len(items) < 2:
                return

            # Extract the sort field values
            values = [getattr(item, sort_by) for item in items]

            # Verify ordering
            for i in range(len(values) - 1):
                if sort_order == "asc":
                    assert values[i] <= values[i + 1], (
                        f"Not ascending at index {i}: {values[i]} > {values[i+1]}"
                    )
                else:
                    assert values[i] >= values[i + 1], (
                        f"Not descending at index {i}: {values[i]} < {values[i+1]}"
                    )
        finally:
            session.close()
            engine.dispose()
