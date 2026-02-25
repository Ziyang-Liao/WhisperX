# WhisperX Batch Speech-to-Text Platform [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE) [![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/) [![NVIDIA CUDA](https://img.shields.io/badge/NVIDIA-CUDA%2012.x-green.svg)](https://developer.nvidia.com/cuda-toolkit) [![WhisperX](https://img.shields.io/badge/WhisperX-large--v3-orange.svg)](https://github.com/m-bain/whisperX) [![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/) [![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)

A full-stack batch speech-to-text platform powered by [WhisperX](https://github.com/m-bain/whisperX). It provides a RESTful API and a web UI for uploading audio files, running GPU-accelerated batch transcription, and browsing results with word-level timestamps.

## Features

- **GPU-Accelerated Transcription** — Automatic CUDA/CPU detection; runs WhisperX `large-v3` with CTranslate2 FP16 on GPU for maximum throughput.
- **Batch Processing** — Queue multiple audio files and transcribe them in a single batch task with progress tracking.
- **Word-Level Alignment** — wav2vec2-based forced alignment produces precise word-level timestamps.
- **Speaker Diarization** — Optional pyannote.audio-based speaker diarization with per-word speaker labels.
- **OOM Auto-Recovery** — On CUDA out-of-memory, automatically halves batch size and retries (up to 3 attempts).
- **Scheduled Transcription** — Cron-based APScheduler integration for automated nightly batch runs.
- **Audio Management** — Upload, replace, delete, search, and paginate audio files via REST API.
- **Web Frontend** — React + TypeScript SPA with audio list, detail view, and task monitoring pages.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (React)                  │
│         Vite · React Router · TanStack Query        │
├─────────────────────────────────────────────────────┤
│                        /api                         │
├─────────────────────────────────────────────────────┤
│                  Backend (FastAPI)                   │
│  ┌──────────┐  ┌────────────────┐  ┌─────────────┐ │
│  │  Audio    │  │  Transcription │  │  Scheduler  │ │
│  │  Manager  │  │  Engine        │  │  (cron)     │ │
│  └────┬─────┘  └───────┬────────┘  └──────┬──────┘ │
│       │                │                   │        │
│  ┌────┴────────────────┴───────────────────┴──────┐ │
│  │           Batch Processor                      │ │
│  └────────────────────┬──────────────────────────-┘ │
│                       │                             │
│  ┌────────────────────┴──────────────────────────┐  │
│  │  WhisperX  ·  faster-whisper  ·  CTranslate2  │  │
│  │  wav2vec2 alignment  ·  pyannote diarization   │  │
│  └────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│              SQLite  ·  File Storage                │
└─────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer    | Technology                                                  |
| -------- | ----------------------------------------------------------- |
| Frontend | React 19, TypeScript, Vite, React Router, TanStack Query    |
| Backend  | Python, FastAPI, SQLAlchemy, APScheduler, Pydantic v2       |
| AI/ML    | WhisperX, faster-whisper, CTranslate2, wav2vec2, pyannote   |
| Database | SQLite (file-based, zero config)                            |
| Runtime  | CUDA 12.x + PyTorch 2.x (GPU) or CPU fallback              |

## Prerequisites

- Python 3.9+
- Node.js 18+
- NVIDIA GPU with CUDA 12.x drivers (recommended) or CPU-only mode
- FFmpeg (required by WhisperX for audio decoding)
- HuggingFace account with access token (required for speaker diarization)

```bash
# Verify GPU availability
nvidia-smi
python3 -c "import torch; print(torch.cuda.is_available())"
```

### Speaker Diarization Setup

WhisperX uses [pyannote.audio](https://github.com/pyannote/pyannote-audio) for speaker diarization. The pyannote models are **gated** — you must manually accept the license terms on HuggingFace before they can be downloaded.

**Step 1:** Create a HuggingFace access token at https://hf.co/settings/tokens

**Step 2:** Accept the user agreement on both model pages (click "Agree and access repository"):
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0

**Step 3:** Configure the token on your machine:

```bash
huggingface-cli login
# Paste your token when prompted
```

> ⚠️ Without completing these steps, speaker diarization will be silently skipped and transcription results will not include speaker labels.

### Alternative Diarization (No HuggingFace Agreement Needed)

If you cannot or prefer not to accept the pyannote gated model terms, the platform also supports a **speechbrain-based** diarization fallback that requires no additional agreements.

| Mode | Model | Clustering | Needs HF Agreement | Auto Speaker Count |
| ---- | ----- | ---------- | ------------------- | ------------------- |
| Mode A (default) | pyannote/speaker-diarization-3.1 | Built-in neural pipeline | ✅ Yes | ✅ Yes |
| Mode B | speechbrain/spkrec-ecapa-voxceleb | Spectral Clustering | ❌ No | ❌ Must specify `n_clusters` |
| Mode C | speechbrain/spkrec-ecapa-voxceleb | Agglomerative Clustering | ❌ No | ✅ Yes (via distance threshold) |

#### Accuracy Comparison

Tested on a synthetic two-speaker English audio (37s, generated by splitting the original single-speaker recording and pitch-shifting one half to simulate a second voice):

| Mode | Correct Segments | Accuracy | Latency | Notes |
| ---- | ---------------- | -------- | ------- | ----- |
| A — pyannote (WhisperX native) | 9 / 11 | **82%** | 2.19s | Best overall; end-to-end neural pipeline with VAD + embedding + clustering |
| B — ECAPA-TDNN + Spectral | 7 / 11 | 64% | 0.84s | Fast; requires knowing speaker count in advance |
| C — ECAPA-TDNN + Agglomerative | 5 / 11 | 45% | <0.01s | No dependencies; sensitive to threshold tuning |

#### When to use which

- **Mode A (pyannote)** — Best accuracy. Use this for production. Requires one-time HuggingFace agreement.
- **Mode B (Spectral)** — Good alternative when you know the number of speakers. No gated model restrictions.
- **Mode C (Agglomerative)** — Simplest setup, zero external dependencies beyond speechbrain. Best for quick prototyping.

#### Mode B/C Usage Example

```python
import torch
import numpy as np
import whisperx
from speechbrain.inference.speaker import EncoderClassifier
from sklearn.cluster import SpectralClustering

device = "cuda"
audio = whisperx.load_audio("meeting.mp3")

# Transcribe + align (same as default pipeline)
model = whisperx.load_model("large-v3", device, compute_type="float16")
result = model.transcribe(audio, batch_size=32)
align_model, meta = whisperx.load_align_model(language_code="en", device=device)
result = whisperx.align(result["segments"], align_model, meta, audio, device)

# Extract speaker embeddings per segment
classifier = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    run_opts={"device": device}
)
embeddings = []
for seg in result["segments"]:
    chunk = audio[int(seg["start"]*16000):int(seg["end"]*16000)]
    wav = torch.tensor(chunk).unsqueeze(0).to(device)
    embeddings.append(classifier.encode_batch(wav).squeeze().cpu().numpy())

emb_matrix = np.stack(embeddings)
emb_matrix = emb_matrix / np.linalg.norm(emb_matrix, axis=1, keepdims=True)

# Cluster into N speakers
from scipy.spatial.distance import cdist
sim = np.clip(1 - cdist(emb_matrix, emb_matrix, metric="cosine"), 0, 1)
labels = SpectralClustering(n_clusters=2, affinity="precomputed").fit_predict(sim)

for seg, label in zip(result["segments"], labels):
    seg["speaker"] = f"SPEAKER_{label:02d}"
```

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Ziyang-Liao/WhisperX.git
cd WhisperX
```

### 2. Backend setup

```bash
cd backend

# Create and activate virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify WhisperX GPU detection
python3 -c "from app.services.transcription_engine import DEFAULT_DEVICE; print('Device:', DEFAULT_DEVICE)"
# Expected output: Device: cuda
```

### 3. Frontend setup

```bash
cd frontend
npm install
```

## Usage

### Start the backend

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API server starts at `http://localhost:8000`. Interactive docs are available at `http://localhost:8000/docs`.

### Start the frontend

```bash
cd frontend
npm run dev
```

The web UI starts at `http://localhost:5173` and proxies API requests to the backend.

### Enable scheduled transcription (optional)

Set the `SCHEDULER_CRON` environment variable to enable automatic batch runs:

```bash
# Run batch transcription daily at 2:00 AM
SCHEDULER_CRON="0 2 * * *" uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API Reference

### Audio Management

| Method   | Endpoint                     | Description                          |
| -------- | ---------------------------- | ------------------------------------ |
| `POST`   | `/api/audio/upload`          | Upload one or more audio files       |
| `GET`    | `/api/audio`                 | List audio files (paginated, search) |
| `GET`    | `/api/audio/{id}`            | Get audio file details               |
| `PUT`    | `/api/audio/{id}`            | Replace an audio file                |
| `DELETE` | `/api/audio/{id}`            | Delete an audio file                 |
| `GET`    | `/api/audio/{id}/transcript` | Get transcription result             |

### Transcription Tasks

| Method | Endpoint                         | Description                    |
| ------ | -------------------------------- | ------------------------------ |
| `POST` | `/api/transcription/trigger`     | Trigger a batch transcription  |
| `GET`  | `/api/transcription/tasks`       | List tasks (paginated)         |
| `GET`  | `/api/transcription/tasks/{id}`  | Get task details               |

### Examples

**Upload audio files:**

```bash
curl -X POST http://localhost:8000/api/audio/upload \
  -F "files=@recording1.mp3" \
  -F "files=@recording2.wav"
```

**Trigger batch transcription:**

```bash
# Transcribe all pending files
curl -X POST http://localhost:8000/api/transcription/trigger

# Transcribe specific files
curl -X POST http://localhost:8000/api/transcription/trigger \
  -H "Content-Type: application/json" \
  -d '{"file_ids": [1, 2, 3]}'
```

**Get transcription result:**

```bash
curl http://localhost:8000/api/audio/1/transcript
```

Response:

```json
{
  "segments": [
    {
      "text": "Hello, welcome to the meeting.",
      "start": 0.0,
      "end": 2.34,
      "speaker": "SPEAKER_00",
      "words": [
        { "word": "Hello,", "start": 0.0, "end": 0.45, "score": 0.98, "speaker": "SPEAKER_00" },
        { "word": "welcome", "start": 0.52, "end": 0.91, "score": 0.97, "speaker": "SPEAKER_00" }
      ]
    }
  ],
  "language": "en",
  "duration": 125.6
}
```

## Supported Audio Formats

`wav` · `mp3` · `flac` · `m4a` · `ogg`

## Language Support

WhisperX `large-v3` supports **99 languages** with automatic detection. The engine analyzes the first 30 seconds of audio to identify the language — no manual configuration needed.

Top supported languages and their typical accuracy:

| Language | Code | Auto-Detection | Notes |
| -------- | ---- | -------------- | ----- |
| English | `en` | ✅ Excellent | Best accuracy overall |
| Chinese (Mandarin) | `zh` | ✅ Excellent | Simplified & Traditional |
| Japanese | `ja` | ✅ Excellent | |
| Korean | `ko` | ✅ Excellent | |
| German | `de` | ✅ Excellent | |
| French | `fr` | ✅ Excellent | |
| Spanish | `es` | ✅ Excellent | |
| Russian | `ru` | ✅ Good | |
| Arabic | `ar` | ✅ Good | |
| Hindi | `hi` | ✅ Good | |
| Portuguese | `pt` | ✅ Good | |
| Italian | `it` | ✅ Good | |
| ... | ... | ... | 99 languages total |

If you know the language in advance, you can skip auto-detection (saves ~1s) by specifying it in the `TranscriptionEngine`:

```python
# Auto-detect (default)
result = engine.transcribe("audio.mp3")

# Manual override — skips detection, slightly faster
model.transcribe(audio, batch_size=32, language="zh")
```

## GPU Configuration

The engine auto-detects the best device at startup:

| Environment       | Device | Compute Type | Batch Size |
| ----------------- | ------ | ------------ | ---------- |
| NVIDIA GPU (CUDA) | `cuda` | `float16`    | 32         |
| CPU only          | `cpu`  | `int8`       | 4          |

The WhisperX `large-v3` model requires approximately 6 GB of VRAM in FP16 mode. A GPU with at least 8 GB VRAM is recommended; 24 GB (e.g., A10G, RTX 4090) enables larger batch sizes for higher throughput.

## Project Structure

```
WhisperX/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI application entry point
│   │   ├── models/                  # SQLAlchemy ORM models
│   │   │   ├── database.py          # Database engine & session
│   │   │   ├── audio_file.py        # AudioFile model
│   │   │   ├── transcription_task.py# TranscriptionTask model
│   │   │   └── task_file_record.py  # Task-File association model
│   │   ├── schemas/                 # Pydantic request/response schemas
│   │   ├── routers/                 # API route handlers
│   │   │   ├── audio.py             # /api/audio endpoints
│   │   │   └── transcription.py     # /api/transcription endpoints
│   │   └── services/                # Business logic
│   │       ├── transcription_engine.py  # WhisperX wrapper
│   │       ├── batch_processor.py       # Batch task execution
│   │       ├── audio_manager.py         # File CRUD operations
│   │       └── scheduler.py            # Cron-based scheduling
│   ├── tests/                       # Backend test suite
│   ├── data/                        # SQLite DB & uploaded files
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # Root component & routing
│   │   ├── pages/                   # Page components
│   │   ├── api/                     # API client functions
│   │   └── components/              # Shared UI components
│   ├── package.json
│   └── vite.config.ts
└── README.md
```

## Testing

### Backend

```bash
cd backend
pytest -v
```

### Frontend

```bash
cd frontend
npm test
```

## Demo: End-to-End Test

The following is a real test run on an AWS EC2 instance with an NVIDIA A10G GPU (24 GB VRAM), demonstrating the full pipeline from audio upload to transcription with speaker diarization.

### Environment

| Item | Value |
| ---- | ----- |
| Instance | AWS EC2 (NVIDIA A10G, 24 GB VRAM) |
| CUDA | 12.8 |
| PyTorch | 2.8.0+cu128 |
| WhisperX | 3.7.5 (large-v3, FP16) |
| Diarization | pyannote/speaker-diarization-3.1 |
| Python | 3.9 |

### Step 1 — Start the backend

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 2 — Upload audio file

Test audio: a 41.7-second English phone call recording from [Pixabay](https://pixabay.com/).

```bash
curl -X POST http://localhost:8000/api/audio/upload \
  -F "files=@test_audio.mp3"
```

```json
[
    {
        "id": 1,
        "filename": "test_audio.mp3",
        "file_size": 833280,
        "duration": 41.664,
        "format": "mp3",
        "upload_time": "2026-02-25T02:27:24.936461",
        "transcription_status": "pending"
    }
]
```

### Step 3 — Trigger batch transcription

```bash
curl -X POST http://localhost:8000/api/transcription/trigger \
  -H "Content-Type: application/json" \
  -d '{"file_ids": [1]}'
```

```json
{
    "id": 1,
    "trigger_type": "manual",
    "status": "completed",
    "total_files": 1,
    "processed_files": 1,
    "success_count": 1,
    "failure_count": 0,
    "started_at": "2026-02-25T02:28:06.038811",
    "completed_at": "2026-02-25T02:28:18.802035",
    "duration_seconds": 12.76
}
```

41.7 seconds of audio transcribed in 12.76 seconds (3.3x real-time) on a single A10G GPU.

### Step 4 — Get transcription result

```bash
curl http://localhost:8000/api/audio/1/transcript
```

```
Language: en | Duration: 41.7s | Segments: 14

[ 0.03s -  0.69s]  Hello.
[ 0.89s -  4.46s]  Refund the headphones, okay?
[ 4.48s -  5.24s]  Listen here, buddy.
[ 5.52s -  8.08s]  My sister wants her stupid headphones fixed.
[ 8.72s - 11.90s]  So you will refund them and send her a new pair.
[13.43s - 15.29s]  Can you put the manager on, please?
[16.35s - 16.97s]  Is this the manager?
[17.89s - 21.28s]  Give my sister a refund on her headphones.
[21.30s - 24.62s]  I have a guarantee it says you must give me a refund.
[25.14s - 27.74s]  Without it, you're breaking the law of false advertising.
[28.09s - 31.40s]  If you want me to continue to prosecute you, I shall do so.
[32.11s - 36.08s]  Now, without further ado, will you please refund my sister's headphones?
[37.28s - 38.69s]  No, no, no!
[39.35s - 41.50s]  Refund the headphones now!
```

### Step 5 — Word-level timestamps

Each word includes a precise timestamp and confidence score:

```
 0.03s -  0.69s  [0.95]  Hello.
 0.89s -  1.29s  [0.79]  Refund
 1.31s -  1.37s  [0.88]  the
 1.45s -  2.09s  [0.89]  headphones,
 4.48s -  4.74s  [0.90]  Listen
 4.78s -  4.96s  [0.70]  here,
 4.98s -  5.24s  [0.88]  buddy.
 ...
40.11s - 40.69s  [0.77]  headphones
40.97s - 41.50s  [0.73]  now!
```

### Step 6 — Speaker diarization

Using WhisperX's built-in pyannote speaker-diarization-3.1:

```python
engine = TranscriptionEngine()
result = engine.transcribe("test_audio.mp3", enable_diarization=True)
```

**Single-speaker result** (this test audio is a one-sided phone call — only the caller was recorded):

```
Language: en | Duration: 41.7s | Segments: 14
Speakers: 1 — SPEAKER_00

[ 0.0s -  0.7s]  SPEAKER_00:  Hello.
[ 0.9s -  4.5s]  SPEAKER_00:  Refund the headphones, okay?
[ 4.5s -  5.2s]  SPEAKER_00:  Listen here, buddy.
[ 5.5s -  8.1s]  SPEAKER_00:  My sister wants her stupid headphones fixed.
[ 8.7s - 11.9s]  SPEAKER_00:  So you will refund them and send her a new pair.
[13.4s - 15.3s]  SPEAKER_00:  Can you put the manager on, please?
[16.4s - 17.0s]  SPEAKER_00:  Is this the manager?
[17.9s - 21.3s]  SPEAKER_00:  Give my sister a refund on her headphones.
[21.3s - 24.6s]  SPEAKER_00:  I have a guarantee it says you must give me a refund.
[25.1s - 27.7s]  SPEAKER_00:  Without it, you're breaking the law of false advertising.
[28.1s - 31.4s]  SPEAKER_00:  If you want me to continue to prosecute you, I shall do so.
[32.1s - 36.1s]  SPEAKER_00:  Now, without further ado, will you please refund my sister's headphones?
[37.3s - 38.7s]  SPEAKER_00:  No, no, no!
[39.3s - 41.5s]  SPEAKER_00:  Refund the headphones now!
```

pyannote correctly identified only 1 speaker — the caller. The customer service agent's voice was not captured in this one-sided recording.

**Multi-speaker result** (tested with a synthetic two-speaker audio — generated by splitting the original recording and pitch-shifting one half to simulate a second voice, 37s):

```
Language: en | Duration: 37.0s | Segments: 11
Speakers: 2 — SPEAKER_00, SPEAKER_01

[ 0.0s -  2.3s]  SPEAKER_00:  Hello, refund the headphones, okay?
[ 4.5s -  9.5s]  SPEAKER_01:  Listen here, folks.
[ 9.5s - 10.7s]  SPEAKER_01:  I have a guarantee it's safe.
[10.7s - 13.9s]  SPEAKER_00:  So you will refund them and send her a new pair.
[15.4s - 18.1s]  SPEAKER_00:  Can you put the manager on, please?
[18.9s - 22.9s]  SPEAKER_00:  You must give me a refund.
[22.9s - 24.1s]  SPEAKER_00:  Without it, you're breaking the law.
[24.1s - 24.3s]  SPEAKER_00:  Please.
[25.4s - 26.0s]  SPEAKER_00:  Is this the manager?
[26.9s - 29.3s]  SPEAKER_00:  Give my sister a refund on her headphones.
[35.4s - 35.9s]  SPEAKER_01:  Yeah.
```

With multi-speaker audio, pyannote correctly assigns distinct labels (SPEAKER_00, SPEAKER_01) to each participant.

### Performance Summary

| Metric | Value |
| ------ | ----- |
| Audio duration | 41.7s |
| Transcription time | 12.76s |
| Real-time factor | 3.3x faster than real-time |
| Model | WhisperX large-v3 (FP16) |
| GPU | NVIDIA A10G (24 GB) |
| Batch size | 32 |
| Word count | 90 words with timestamps |
| Segments | 14 |
| Language detected | English (confidence: 1.00) |

## License

Apache 2.0 - See [LICENSE](LICENSE) for details.

## Citation

```bibtex
@software{whisperx_batch_platform,
  title  = {WhisperX Batch Speech-to-Text Platform},
  author = {Ziyang Liao},
  year   = {2026},
  url    = {https://github.com/Ziyang-Liao/WhisperX}
}
```
