# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-26

### Changed

- **Diarization re-segmentation** — Replaced WhisperX default `assign_word_speakers` (majority-vote on coarse ASR segments) with diarization-boundary re-segmentation. Each word is now assigned a speaker via maximum time overlap with pyannote output, and consecutive same-speaker words are grouped into new segments. This preserves speaker switches that were previously lost in long ASR segments, with no VAD parameter tuning required.

### Fixed

- Multi-speaker detection now correctly identifies all speakers in continuous speech where ASR produces few, long segments (e.g., 29s of speech as a single segment).

## [0.1.0] - 2026-02-25

### Added

- **GPU-accelerated transcription** — WhisperX `large-v3` with CTranslate2 FP16 on CUDA, automatic CPU fallback with INT8.
- **Batch processing** — Queue multiple audio files and transcribe in a single task with progress tracking.
- **Word-level alignment** — wav2vec2-based forced alignment for precise word-level timestamps.
- **Speaker diarization** — Optional pyannote.audio speaker-diarization-3.1 integration with per-word speaker labels.
- **Alternative diarization modes** — SpeechBrain ECAPA-TDNN fallback with Spectral or Agglomerative clustering (no HuggingFace agreement needed).
- **OOM auto-recovery** — On CUDA out-of-memory, automatically halves batch size and retries (up to 3 attempts).
- **Scheduled transcription** — Cron-based APScheduler integration for automated batch runs.
- **REST API** — FastAPI endpoints for audio upload, management, transcription triggering, and result retrieval.
- **Web frontend** — React 19 + TypeScript SPA with audio list, detail view, and task monitoring.
- **99-language support** — Automatic language detection via WhisperX (analyzes first 30s of audio).
- **Python 3.9 compatibility** — Backported type hints and runtime fixes for Python 3.9+.
- Apache 2.0 license.

[0.2.0]: https://github.com/Ziyang-Liao/WhisperX/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Ziyang-Liao/WhisperX/releases/tag/v0.1.0
