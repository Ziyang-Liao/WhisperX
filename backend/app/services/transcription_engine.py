from __future__ import annotations
"""Transcription Engine service wrapping WhisperX for speech-to-text processing."""

import logging
from dataclasses import dataclass, field

from app.schemas.transcript import TranscriptResult, TranscriptSegment, WordSegment

logger = logging.getLogger(__name__)

# Default configuration — auto-detect device
import platform as _platform
import torch as _torch

def _detect_device() -> tuple[str, str]:
    """Auto-detect best device and compute type for the current platform."""
    if _torch.cuda.is_available():
        return "cuda", "float16"
    if hasattr(_torch.backends, "mps") and _torch.backends.mps.is_available():
        return "cpu", "int8"  # whisperx/ctranslate2 doesn't support MPS directly, use CPU with int8
    return "cpu", "int8"

_default_device, _default_compute = _detect_device()

DEFAULT_MODEL = "large-v3"
DEFAULT_DEVICE = _default_device
DEFAULT_COMPUTE_TYPE = _default_compute
DEFAULT_BATCH_SIZE = 4 if _default_device == "cpu" else 32
MAX_RETRY_ATTEMPTS = 3
MIN_BATCH_SIZE = 1


@dataclass
class TranscriptionEngine:
    """WhisperX-based transcription engine with VAD, alignment, and optional diarization."""

    model_name: str = DEFAULT_MODEL
    device: str = DEFAULT_DEVICE
    compute_type: str = DEFAULT_COMPUTE_TYPE
    batch_size: int = DEFAULT_BATCH_SIZE
    _model: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Defer model loading until first use (lazy initialization)."""
        pass

    def _ensure_model_loaded(self) -> None:
        """Load the model if not already loaded."""
        if self._model is None:
            self._load_model()

    def _load_model(self) -> None:
        """Load WhisperX model with faster-whisper backend and CTranslate2 fp16."""
        import whisperx

        logger.info(
            "Loading WhisperX model=%s device=%s compute_type=%s",
            self.model_name,
            self.device,
            self.compute_type,
        )
        self._model = whisperx.load_model(
            self.model_name,
            self.device,
            compute_type=self.compute_type,
        )

    def transcribe(
        self, audio_path: str, enable_diarization: bool = False
    ) -> TranscriptResult:
        """
        Transcribe a single audio file.

        Pipeline: load audio → VAD + batch inference → wav2vec2 alignment
                  → optional pyannote diarization → TranscriptResult

        Args:
            audio_path: Path to the audio file.
            enable_diarization: Whether to run speaker diarization.

        Returns:
            Structured TranscriptResult with segments, word-level timestamps,
            and optional speaker labels.
        """
        import whisperx

        self._ensure_model_loaded()

        # 1. Load audio
        audio = whisperx.load_audio(audio_path)
        audio_duration = len(audio) / 16000  # whisperx loads at 16kHz

        # 2. VAD preprocessing + batch transcription
        result = self._model.transcribe(audio, batch_size=self.batch_size)
        detected_language = result.get("language", "unknown")

        # 3. wav2vec2 word-level alignment
        try:
            align_model, align_metadata = whisperx.load_align_model(
                language_code=detected_language, device=self.device
            )
            result = whisperx.align(
                result["segments"],
                align_model,
                align_metadata,
                audio,
                self.device,
                return_char_alignments=False,
            )
        except Exception as exc:
            logger.warning(
                "wav2vec2 alignment failed, returning unaligned result: %s", exc
            )
            # Fall through with unaligned segments

        # 4. Optional speaker diarization via pyannote
        if enable_diarization:
            try:
                diarize_model = whisperx.DiarizationPipeline(device=self.device)
                diarize_segments = diarize_model(audio_path)
                result = whisperx.assign_word_speakers(diarize_segments, result)
            except Exception as exc:
                logger.warning(
                    "Speaker diarization failed, returning without speakers: %s", exc
                )

        # 5. Build TranscriptResult
        return self._build_result(result, detected_language, audio_duration)

    def transcribe_with_retry(
        self, audio_path: str, enable_diarization: bool = False
    ) -> TranscriptResult:
        """
        Transcribe with automatic retry on OOM errors.

        On CUDA OOM, halves the batch_size and retries up to MAX_RETRY_ATTEMPTS times.
        Restores the original batch_size after completion (success or final failure).
        """
        original_batch_size = self.batch_size

        try:
            for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
                try:
                    result = self.transcribe(audio_path, enable_diarization)
                    return result
                except (RuntimeError, Exception) as exc:
                    if not self._is_oom_error(exc):
                        raise

                    new_batch_size = max(MIN_BATCH_SIZE, self.batch_size // 2)
                    logger.warning(
                        "OOM on attempt %d/%d, reducing batch_size %d → %d",
                        attempt,
                        MAX_RETRY_ATTEMPTS,
                        self.batch_size,
                        new_batch_size,
                    )

                    if new_batch_size == self.batch_size:
                        logger.error("batch_size already at minimum, giving up")
                        raise

                    self.batch_size = new_batch_size

                    if attempt == MAX_RETRY_ATTEMPTS:
                        logger.error("Max retry attempts reached")
                        raise

            raise RuntimeError("Transcription failed after retries")
        finally:
            self.batch_size = original_batch_size

    @staticmethod
    def _is_oom_error(exc: BaseException) -> bool:
        """Check if an exception is a CUDA out-of-memory error."""
        msg = str(exc).lower()
        return "out of memory" in msg or ("cuda" in msg and "oom" in msg)

    @staticmethod
    def _build_result(
        raw_result: dict, language: str, duration: float
    ) -> TranscriptResult:
        """Convert raw WhisperX output dict into a TranscriptResult."""
        segments: list[TranscriptSegment] = []

        for seg in raw_result.get("segments", []):
            words: list[WordSegment] = []
            for w in seg.get("words", []):
                words.append(
                    WordSegment(
                        word=w.get("word", ""),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        score=w.get("score", 0.0),
                        speaker=w.get("speaker"),
                    )
                )

            segments.append(
                TranscriptSegment(
                    text=seg.get("text", ""),
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    speaker=seg.get("speaker"),
                    words=words,
                )
            )

        return TranscriptResult(
            segments=segments,
            language=language,
            duration=duration,
        )
