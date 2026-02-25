# Feature: batch-speech-to-text, Property 6: 批量转录仅收集待转录文件
# Feature: batch-speech-to-text, Property 7: 单文件失败不影响批量任务
# Feature: batch-speech-to-text, Property 8: 任务结果计数不变量
# Feature: batch-speech-to-text, Property 9: 手动触发仅处理选定文件
# Feature: batch-speech-to-text, Property 10: 批量任务互斥

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.audio_file import AudioFile
from app.models.database import Base
from app.models.task_file_record import TaskFileRecord
from app.models.transcription_task import TranscriptionTask
from app.schemas.transcript import TranscriptResult
from app.services.batch_processor import BatchProcessor


# --- Fixtures & helpers ---

def _make_db(tmp_dir: str):
    """Create a fresh SQLite DB session."""
    db_url = f"sqlite:///{tmp_dir}/test.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session(), engine


def _make_audio(session, filename="test.wav", status="pending", stored_path="/tmp/f.wav"):
    """Insert an AudioFile record and return it."""
    record = AudioFile(
        filename=filename,
        stored_path=stored_path,
        file_size=1000,
        duration=5.0,
        format="wav",
        transcription_status=status,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _mock_engine_success():
    """Create a mock TranscriptionEngine that always succeeds."""
    engine = MagicMock()
    engine.transcribe_with_retry.return_value = TranscriptResult(
        segments=[], language="en", duration=5.0
    )
    return engine


def _mock_engine_selective_failure(fail_paths: set[str]):
    """Create a mock engine that fails for specific stored_path values."""
    engine = MagicMock()

    def side_effect(audio_path, *args, **kwargs):
        if audio_path in fail_paths:
            raise RuntimeError(f"Simulated failure for {audio_path}")
        return TranscriptResult(segments=[], language="en", duration=5.0)

    engine.transcribe_with_retry.side_effect = side_effect
    return engine


# --- Strategies ---

status_strategy = st.sampled_from(["pending", "processing", "completed", "failed"])


# --- Property 6: 批量转录仅收集待转录文件 ---

@settings(max_examples=100)
@given(
    statuses=st.lists(status_strategy, min_size=1, max_size=10),
)
def test_property_6_batch_only_collects_pending_files(statuses):
    """Property 6: When file_ids is None, only 'pending' files are processed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine_db = _make_db(tmp_dir)
        try:
            audio_ids_by_status: dict[str, list[int]] = {
                "pending": [], "processing": [], "completed": [], "failed": []
            }
            for i, status in enumerate(statuses):
                rec = _make_audio(
                    session,
                    filename=f"file_{i}.wav",
                    status=status,
                    stored_path=f"/tmp/audio_{i}.wav",
                )
                audio_ids_by_status[status].append(rec.id)

            mock_engine = _mock_engine_success()
            processor = BatchProcessor(session, mock_engine)
            task = processor.run_batch(file_ids=None, trigger_type="scheduled")

            # Only pending files should have been processed
            pending_count = len(audio_ids_by_status["pending"])
            assert task.total_files == pending_count
            assert task.processed_files == pending_count
            assert task.success_count == pending_count
            assert task.failure_count == 0

            # Non-pending files should remain unchanged
            for status in ["processing", "completed", "failed"]:
                for aid in audio_ids_by_status[status]:
                    rec = session.query(AudioFile).filter_by(id=aid).first()
                    assert rec.transcription_status == status
        finally:
            session.close()
            engine_db.dispose()


# --- Property 7: 单文件失败不影响批量任务 ---

@settings(max_examples=100)
@given(
    num_files=st.integers(min_value=2, max_value=8),
    fail_indices=st.lists(st.integers(min_value=0, max_value=7), min_size=1, max_size=4, unique=True),
)
def test_property_7_single_failure_does_not_block_others(num_files, fail_indices):
    """Property 7: If some files fail, subsequent files are still processed."""
    # Ensure fail indices are within range
    fail_indices = [i for i in fail_indices if i < num_files]
    assume(len(fail_indices) > 0)
    assume(len(fail_indices) < num_files)  # At least one should succeed

    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine_db = _make_db(tmp_dir)
        try:
            audio_records = []
            for i in range(num_files):
                rec = _make_audio(
                    session,
                    filename=f"file_{i}.wav",
                    status="pending",
                    stored_path=f"/tmp/audio_{i}.wav",
                )
                audio_records.append(rec)

            fail_paths = {audio_records[i].stored_path for i in fail_indices}
            mock_engine = _mock_engine_selective_failure(fail_paths)
            processor = BatchProcessor(session, mock_engine)

            file_ids = [r.id for r in audio_records]
            task = processor.run_batch(file_ids=file_ids)

            # All files should have been processed (attempted)
            assert task.processed_files == num_files
            assert task.total_files == num_files

            # Verify correct success/failure split
            expected_failures = len(fail_indices)
            expected_successes = num_files - expected_failures
            assert task.failure_count == expected_failures
            assert task.success_count == expected_successes

            # Verify individual file statuses
            for i, rec in enumerate(audio_records):
                session.refresh(rec)
                if i in fail_indices:
                    assert rec.transcription_status == "failed"
                    assert rec.error_message is not None
                else:
                    assert rec.transcription_status == "completed"
        finally:
            session.close()
            engine_db.dispose()


# --- Property 8: 任务结果计数不变量 ---

@settings(max_examples=100)
@given(
    num_files=st.integers(min_value=1, max_value=10),
    fail_indices=st.lists(st.integers(min_value=0, max_value=9), min_size=0, max_size=5, unique=True),
)
def test_property_8_task_count_invariant(num_files, fail_indices):
    """Property 8: success_count + failure_count == total_files and duration_seconds > 0."""
    fail_indices = [i for i in fail_indices if i < num_files]

    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine_db = _make_db(tmp_dir)
        try:
            audio_records = []
            for i in range(num_files):
                rec = _make_audio(
                    session,
                    filename=f"file_{i}.wav",
                    status="pending",
                    stored_path=f"/tmp/audio_{i}.wav",
                )
                audio_records.append(rec)

            fail_paths = {audio_records[i].stored_path for i in fail_indices}
            mock_engine = _mock_engine_selective_failure(fail_paths)
            processor = BatchProcessor(session, mock_engine)

            file_ids = [r.id for r in audio_records]
            task = processor.run_batch(file_ids=file_ids)

            # Invariant: success + failure == total
            assert task.success_count + task.failure_count == task.total_files
            assert task.total_files == num_files
            assert task.processed_files == num_files

            # Duration must be positive
            assert task.duration_seconds is not None
            assert task.duration_seconds >= 0

            # Task should be completed
            assert task.status == "completed"
            assert task.completed_at is not None
        finally:
            session.close()
            engine_db.dispose()


# --- Property 9: 手动触发仅处理选定文件 ---

@settings(max_examples=100)
@given(
    num_total=st.integers(min_value=2, max_value=10),
    data=st.data(),
)
def test_property_9_manual_trigger_only_processes_selected(num_total, data):
    """Property 9: When file_ids is specified, only those files are processed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine_db = _make_db(tmp_dir)
        try:
            all_records = []
            for i in range(num_total):
                rec = _make_audio(
                    session,
                    filename=f"file_{i}.wav",
                    status="pending",
                    stored_path=f"/tmp/audio_{i}.wav",
                )
                all_records.append(rec)

            # Pick a random non-empty subset
            subset_size = data.draw(st.integers(min_value=1, max_value=num_total))
            selected_indices = data.draw(
                st.lists(
                    st.integers(min_value=0, max_value=num_total - 1),
                    min_size=subset_size,
                    max_size=subset_size,
                    unique=True,
                )
            )
            selected_ids = [all_records[i].id for i in selected_indices]
            unselected_ids = [
                r.id for i, r in enumerate(all_records) if i not in selected_indices
            ]

            mock_engine = _mock_engine_success()
            processor = BatchProcessor(session, mock_engine)
            task = processor.run_batch(file_ids=selected_ids)

            # Only selected files processed
            assert task.total_files == len(selected_ids)
            assert task.processed_files == len(selected_ids)
            assert task.success_count == len(selected_ids)

            # Selected files should be completed
            for aid in selected_ids:
                rec = session.query(AudioFile).filter_by(id=aid).first()
                assert rec.transcription_status == "completed"

            # Unselected files should remain pending
            for aid in unselected_ids:
                rec = session.query(AudioFile).filter_by(id=aid).first()
                assert rec.transcription_status == "pending"
        finally:
            session.close()
            engine_db.dispose()


# --- Property 10: 批量任务互斥 ---

@settings(max_examples=100)
@given(
    trigger_type=st.sampled_from(["manual", "scheduled"]),
)
def test_property_10_batch_task_mutual_exclusion(trigger_type):
    """Property 10: New batch requests are rejected when a task is already running."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine_db = _make_db(tmp_dir)
        try:
            # Create a running task in the DB
            running_task = TranscriptionTask(
                trigger_type="manual",
                status="running",
                total_files=5,
                processed_files=2,
                success_count=2,
                failure_count=0,
            )
            session.add(running_task)
            session.commit()

            # Add a pending file
            _make_audio(session, filename="new.wav", status="pending")

            mock_engine = _mock_engine_success()
            processor = BatchProcessor(session, mock_engine)

            # Verify is_running returns True
            assert processor.is_running() is True

            # New batch request should be rejected
            with pytest.raises(PermissionError):
                processor.run_batch(trigger_type=trigger_type)

            # Engine should never have been called
            mock_engine.transcribe_with_retry.assert_not_called()
        finally:
            session.close()
            engine_db.dispose()


@settings(max_examples=100)
@given(
    completed_count=st.integers(min_value=0, max_value=5),
)
def test_property_10_allows_new_task_when_none_running(completed_count):
    """Property 10: New batch is allowed when no task is running (only completed/failed)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session, engine_db = _make_db(tmp_dir)
        try:
            # Create completed tasks
            for i in range(completed_count):
                t = TranscriptionTask(
                    trigger_type="manual",
                    status="completed",
                    total_files=1,
                    processed_files=1,
                    success_count=1,
                    failure_count=0,
                )
                session.add(t)
            session.commit()

            _make_audio(session, filename="file.wav", status="pending",
                        stored_path="/tmp/audio_0.wav")

            mock_engine = _mock_engine_success()
            processor = BatchProcessor(session, mock_engine)

            assert processor.is_running() is False

            # Should succeed
            task = processor.run_batch()
            assert task.status == "completed"
        finally:
            session.close()
            engine_db.dispose()
