# Phase 1 Handoff — Video Subtitle Platform

What was built and deployed in Phase 1, how it works, and how to operate it.
Companion to `docs/subtitle-platform-design.md` (the design) and `deploy/README.md`
(the deploy mechanics).

## Summary

The audio-only transcription platform now has a **video → multi-language soft
subtitle** pipeline, with media stored in **S3** and translation via **Claude
Haiku 4.5 on Amazon Bedrock**. Transcription and translation run in a background
worker (no longer synchronously in the request). Deployed to the `temp-account`
AWS account on a CPU instance (the account has no GPU quota).

## What changed in the code

### New services (`backend/app/services/`)
| Module | Responsibility |
|---|---|
| `storage.py` | `StorageService` over S3: presigned PUT/GET, put/download, delete-by-prefix. Key layout `videos/{id}/…`, `subtitles/{id}/{lang}.{srt,vtt}`. |
| `subtitles.py` | SRT/VTT serialization from timestamped cues + `extract_audio()` (ffmpeg → 16 kHz mono wav). |
| `translation_engine.py` | `TranslationEngine` (Bedrock Claude). Per-segment translation with **1:1 count validation** and split-and-retry; reuses source timestamps. |
| `subtitle_pipeline.py` | `SubtitlePipeline`: stage 1 transcribe (→ source track), stage 2 translate (→ target track). Dependency-injected; fully unit-tested with fakes. |
| `worker.py` | `SubtitleWorker`: single daemon thread, model loaded once, drives the pipeline off DB state. |

### New models (`backend/app/models/`)
- `MediaFile` — video/audio in S3 (`s3_key`, `media_type`, `source_language`, `transcript_json`).
- `SubtitleTrack` — one language per row (`is_source`, `status`, `srt_s3_key`, `vtt_s3_key`); unique `(media_file_id, language)`.

### New API (`backend/app/routers/media.py`, prefix `/api/media`)
Create (presigned upload) → complete → list/get/stream/delete → generate subtitles
(multi-language, skips source) → list/download subtitles → batch generate.

### Blocking-bug fixes from Phase 0 (still in place)
- Added `GET /api/audio/{id}/file` (player 404).
- `TranscriptionEngine` is a module-level singleton (no per-request model reload).
- `BatchProcessor` uses a module-level lock (real mutual exclusion).

## Tests

`backend/tests/` adds `test_storage.py` (moto), `test_subtitles.py`,
`test_translation_engine.py` (fake Bedrock — covers alignment + split-retry),
`test_subtitle_pipeline.py`, `test_media_api.py`.

Run: `cd backend && python3 -m pytest -q`
Result at handoff: **78 passed, 1 skipped** (moto not installed locally), **2 pre-existing
failures** unrelated to this work (`test_property_6_batch_only_collects_pending_files`
expects pending-only but the code also retries failed files; `test_diarization_success_assigns_speakers`
has a mock-setup gap for `whisperx.diarize`). Both fail on the original `main` too.

## Account-specific constraints (the deployment AWS account)

- **GPU quota = 0** → WhisperX runs on **CPU** (`large-v3`, int8).
- **Opus not available** → translation uses **Haiku 4.5**.
- **Bedrock requires cross-region inference profiles** (`us.` prefix); bare model IDs are rejected.

## Deployed stack

See `deploy/README.md` and `deploy/deploy-state.env`. Created: private S3 bucket,
least-privilege IAM role/profile (S3 + `bedrock:InvokeModel` on Haiku), security
group (TCP 8000), `c7i.2xlarge` CPU instance running the app via systemd
(`whisperx.service`) + the background worker.

- **Provision:** `cd deploy && bash deploy.sh`
- **Tear down:** `cd deploy && bash teardown.sh`
- **Stop cost when idle:** `aws --profile temp-account ec2 stop-instances --instance-ids <id>`

> ⚠️ The bootstrap installs a **static ffmpeg** build — AL2023's repos don't carry `ffmpeg`.

## Network architecture (front door)

The instance is **not** exposed directly. CloudFront is the only public entry:

```
viewer ──HTTPS──▶ CloudFront ──HTTP + X-Origin-Secret──▶ EC2 :8000
```

- EC2 security group allows `:8000` **only** from CloudFront's managed
  origin-facing prefix list (`pl-3b927c52`) — direct public TCP is dropped
  (verified: hitting the instance IP returns HTTP 000).
- CloudFront injects an `X-Origin-Secret` header; the app (`ORIGIN_SECRET` env)
  403s anything without it — defends against other CloudFront tenants, since the
  prefix list is shared.
- Run `deploy/front-door.sh` after `deploy.sh` to create this. See `deploy/README.md`.

Caveat: no custom domain, so CloudFront→origin is HTTP (viewer→CloudFront is
HTTPS). End-to-end TLS needs a domain + ACM + ALB — a Phase 4 item.

## Known gaps / next phases

- **No app-level user auth yet** (Phase 4) — the origin is locked to CloudFront,
  but there's no per-user login. Add Cognito/API keys + a custom domain with
  end-to-end TLS (domain → ACM cert → optionally an ALB) before real production.
- **Frontend** not yet updated for video upload / language multi-select / `<video><track>` preview (Phase 3).
- **Alembic** migrations not added (new schema starts clean; needed once there's real data).
- SQLite is single-box; move to RDS Postgres if scaling to multiple workers (design §11).
