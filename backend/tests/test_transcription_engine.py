# Tests for TranscriptionEngine: normal flow, OOM retry, alignment fallback, diarization fallback
# Validates: Requirements 7.4, 7.5, 7.7

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np

from app.schemas.transcript import TranscriptResult


# --- Helpers ---

def _make_raw_whisperx_result(language="en"):
    """Build a fake WhisperX transcription result dict."""
    return {
        "language": language,
        "segments": [
            {
                "text": "Hello world",
                "start": 0.0,
                "end": 1.5,
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.7, "score": 0.95},
                    {"word": "world", "start": 0.8, "end": 1.5, "score": 0.90},
                ],
            },
            {
                "text": "Testing speech",
                "start": 2.0,
                "end": 3.5,
                "words": [
                    {"word": "Testing", "start": 2.0, "end": 2.6, "score": 0.88},
                    {"word": "speech", "start": 2.7, "end": 3.5, "score": 0.92},
                ],
            },
        ],
    }


def _make_aligned_result():
    """Build a fake aligned result (same structure, WhisperX align returns same shape)."""
    return {
        "segments": [
            {
                "text": "Hello world",
                "start": 0.0,
                "end": 1.5,
                "speaker": None,
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.7, "score": 0.95},
                    {"word": "world", "start": 0.8, "end": 1.5, "score": 0.90},
                ],
            },
            {
                "text": "Testing speech",
                "start": 2.0,
                "end": 3.5,
                "speaker": None,
                "words": [
                    {"word": "Testing", "start": 2.0, "end": 2.6, "score": 0.88},
                    {"word": "speech", "start": 2.7, "end": 3.5, "score": 0.92},
                ],
            },
        ],
    }


def _make_diarized_result():
    """Build a fake result after speaker assignment."""
    result = _make_aligned_result()
    for seg in result["segments"]:
        seg["speaker"] = "SPEAKER_00"
        for w in seg["words"]:
            w["speaker"] = "SPEAKER_00"
    return result


# --- Test: Normal transcription flow ---

@patch("app.services.transcription_engine.whisperx", create=True)
def test_normal_transcription_flow(mock_whisperx_module):
    """Full pipeline: load audio → transcribe → align → return TranscriptResult."""
    # Setup mocks
    fake_audio = np.zeros(16000 * 4, dtype=np.float32)  # 4 seconds
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    raw_result = _make_raw_whisperx_result()
    mock_whisperx_module.load_model.return_value.transcribe.return_value = raw_result

    align_model = MagicMock()
    align_metadata = MagicMock()
    mock_whisperx_module.load_align_model.return_value = (align_model, align_metadata)
    mock_whisperx_module.align.return_value = _make_aligned_result()

    # Patch the import inside the module
    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        # Bypass __post_init__ model loading by patching _load_model
        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=16)
            engine._model = mock_whisperx_module.load_model.return_value

        result = engine.transcribe("/fake/audio.wav")

    assert isinstance(result, TranscriptResult)
    assert result.language == "en"
    assert result.duration == 4.0
    assert len(result.segments) == 2
    assert result.segments[0].text == "Hello world"
    assert len(result.segments[0].words) == 2
    assert result.segments[0].words[0].word == "Hello"
    assert result.segments[0].words[0].score == 0.95


# --- Test: OOM retry logic (batch_size degradation) ---

@patch("app.services.transcription_engine.whisperx", create=True)
def test_oom_retry_reduces_batch_size(mock_whisperx_module):
    """On OOM, batch_size should halve and retry. Original batch_size restored after."""
    fake_audio = np.zeros(16000 * 2, dtype=np.float32)
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    # First call: OOM, second call: success
    oom_error = RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    raw_result = _make_raw_whisperx_result()
    mock_model = mock_whisperx_module.load_model.return_value
    mock_model.transcribe.side_effect = [oom_error, raw_result]

    mock_whisperx_module.load_align_model.return_value = (MagicMock(), MagicMock())
    mock_whisperx_module.align.return_value = _make_aligned_result()

    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=16)
            engine._model = mock_model

        result = engine.transcribe_with_retry("/fake/audio.wav")

    assert isinstance(result, TranscriptResult)
    # batch_size should be restored to original after success
    assert engine.batch_size == 16


@patch("app.services.transcription_engine.whisperx", create=True)
def test_oom_retry_exhausts_attempts(mock_whisperx_module):
    """After MAX_RETRY_ATTEMPTS OOM failures, should raise and restore batch_size."""
    fake_audio = np.zeros(16000 * 2, dtype=np.float32)
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    oom_error = RuntimeError("CUDA out of memory")
    mock_model = mock_whisperx_module.load_model.return_value
    mock_model.transcribe.side_effect = oom_error  # Always OOM

    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=8)
            engine._model = mock_model

        with pytest.raises(RuntimeError, match="out of memory"):
            engine.transcribe_with_retry("/fake/audio.wav")

    # batch_size should be restored
    assert engine.batch_size == 8


@patch("app.services.transcription_engine.whisperx", create=True)
def test_non_oom_error_not_retried(mock_whisperx_module):
    """Non-OOM errors should propagate immediately without retry."""
    fake_audio = np.zeros(16000 * 2, dtype=np.float32)
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    mock_model = mock_whisperx_module.load_model.return_value
    mock_model.transcribe.side_effect = ValueError("Corrupted audio file")

    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=16)
            engine._model = mock_model

        with pytest.raises(ValueError, match="Corrupted audio"):
            engine.transcribe_with_retry("/fake/audio.wav")

    # Should have only been called once (no retry)
    assert mock_model.transcribe.call_count == 1


# --- Test: wav2vec2 alignment failure fallback ---

@patch("app.services.transcription_engine.whisperx", create=True)
def test_alignment_failure_returns_unaligned_result(mock_whisperx_module):
    """When wav2vec2 alignment fails, should return result without aligned timestamps."""
    fake_audio = np.zeros(16000 * 3, dtype=np.float32)
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    raw_result = _make_raw_whisperx_result()
    mock_whisperx_module.load_model.return_value.transcribe.return_value = raw_result

    # Alignment fails
    mock_whisperx_module.load_align_model.side_effect = RuntimeError(
        "No align model for language: xx"
    )

    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=16)
            engine._model = mock_whisperx_module.load_model.return_value

        result = engine.transcribe("/fake/audio.wav")

    # Should still return a valid result with unaligned data
    assert isinstance(result, TranscriptResult)
    assert len(result.segments) == 2
    assert result.segments[0].text == "Hello world"


# --- Test: pyannote diarization failure fallback ---

@patch("app.services.transcription_engine.whisperx", create=True)
def test_diarization_failure_returns_result_without_speakers(mock_whisperx_module):
    """When pyannote diarization fails, should return result without speaker labels."""
    fake_audio = np.zeros(16000 * 3, dtype=np.float32)
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    raw_result = _make_raw_whisperx_result()
    mock_whisperx_module.load_model.return_value.transcribe.return_value = raw_result

    mock_whisperx_module.load_align_model.return_value = (MagicMock(), MagicMock())
    mock_whisperx_module.align.return_value = _make_aligned_result()

    # Diarization fails
    mock_whisperx_module.DiarizationPipeline.side_effect = RuntimeError(
        "pyannote auth token missing"
    )

    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=16)
            engine._model = mock_whisperx_module.load_model.return_value

        result = engine.transcribe("/fake/audio.wav", enable_diarization=True)

    assert isinstance(result, TranscriptResult)
    assert len(result.segments) == 2
    # Speakers should be None since diarization failed
    for seg in result.segments:
        assert seg.speaker is None


@patch("app.services.transcription_engine.whisperx", create=True)
def test_diarization_success_assigns_speakers(mock_whisperx_module):
    """When diarization succeeds, result should contain speaker labels."""
    fake_audio = np.zeros(16000 * 3, dtype=np.float32)
    mock_whisperx_module.load_audio.return_value = fake_audio
    mock_whisperx_module.load_model.return_value = MagicMock()

    raw_result = _make_raw_whisperx_result()
    mock_whisperx_module.load_model.return_value.transcribe.return_value = raw_result

    mock_whisperx_module.load_align_model.return_value = (MagicMock(), MagicMock())
    mock_whisperx_module.align.return_value = _make_aligned_result()

    mock_diarize = MagicMock()
    mock_whisperx_module.DiarizationPipeline.return_value = mock_diarize
    mock_diarize.return_value = MagicMock()  # diarize_segments
    mock_whisperx_module.assign_word_speakers.return_value = _make_diarized_result()

    with patch.dict("sys.modules", {"whisperx": mock_whisperx_module}):
        from app.services.transcription_engine import TranscriptionEngine

        with patch.object(TranscriptionEngine, "_load_model"):
            engine = TranscriptionEngine(batch_size=16)
            engine._model = mock_whisperx_module.load_model.return_value

        result = engine.transcribe("/fake/audio.wav", enable_diarization=True)

    assert isinstance(result, TranscriptResult)
    for seg in result.segments:
        assert seg.speaker == "SPEAKER_00"
        for w in seg.words:
            assert w.speaker == "SPEAKER_00"


# --- Test: _build_result handles missing/empty fields gracefully ---

def test_build_result_handles_empty_segments():
    """_build_result should handle an empty segments list."""
    from app.services.transcription_engine import TranscriptionEngine

    result = TranscriptionEngine._build_result(
        {"segments": []}, language="en", duration=5.0
    )
    assert isinstance(result, TranscriptResult)
    assert len(result.segments) == 0
    assert result.language == "en"
    assert result.duration == 5.0


def test_build_result_handles_missing_word_fields():
    """_build_result should default missing word fields to safe values."""
    from app.services.transcription_engine import TranscriptionEngine

    raw = {
        "segments": [
            {
                "text": "partial",
                "start": 1.0,
                "end": 2.0,
                "words": [
                    {"word": "partial"},  # missing start, end, score
                ],
            }
        ]
    }
    result = TranscriptionEngine._build_result(raw, language="zh", duration=3.0)
    assert len(result.segments) == 1
    w = result.segments[0].words[0]
    assert w.word == "partial"
    assert w.start == 0.0
    assert w.end == 0.0
    assert w.score == 0.0


# --- Test: _is_oom_error detection ---

def test_is_oom_error_detects_cuda_oom():
    """Should detect CUDA out of memory errors."""
    from app.services.transcription_engine import TranscriptionEngine

    assert TranscriptionEngine._is_oom_error(
        RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    )
    assert TranscriptionEngine._is_oom_error(
        RuntimeError("RuntimeError: CUDA out of memory")
    )


def test_is_oom_error_rejects_non_oom():
    """Should not flag non-OOM errors."""
    from app.services.transcription_engine import TranscriptionEngine

    assert not TranscriptionEngine._is_oom_error(ValueError("bad input"))
    assert not TranscriptionEngine._is_oom_error(FileNotFoundError("no such file"))
