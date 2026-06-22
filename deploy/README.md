# Deployment — WhisperX Subtitle Platform

Deploys the video-subtitling backend to the **temp-account** AWS account only.
Every script asserts the account id (`ACCOUNT_ID_REDACTED`) and refuses to run elsewhere.

## What gets created (all in `temp-account`, us-east-1)

| Resource | Name | Purpose | Cost |
|---|---|---|---|
| S3 bucket | `whisperx-subs-ACCOUNT_ID_REDACTED-us-east-1` | source videos + generated SRT/VTT + app tarball | ~$0.023/GB-mo |
| IAM role + instance profile | `whisperx-subs-ec2-role` / `-profile` | scoped S3 + `bedrock:InvokeModel` (Haiku) | $0 |
| Security group | `whisperx-subs-sg` | allows TCP 8000 (API) | $0 |
| EC2 instance | `whisperx-subs-app` (`c7i.2xlarge`, CPU) | runs FastAPI + background worker + WhisperX | **~$0.36/hr ≈ $8.6/day** |

> GPU quota on this workshop account is **0**, so WhisperX runs on CPU (`large-v3` int8).
> Translation uses **Claude Haiku 4.5 on Bedrock** via cross-region inference profile
> `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Opus is not available on this account).

## Usage

```bash
cd deploy
bash deploy.sh        # create everything; writes deploy-state.env, prints the App URL
bash teardown.sh      # remove everything it created
```

Configuration lives in `config.env` (region, instance type, model id, names).

## After deploy

- `http://<public-ip>:8000/health` — liveness
- `http://<public-ip>:8000/docs` — OpenAPI UI to exercise the API
- First boot installs torch (CPU) + whisperx and downloads the model (~5–10 min).
  The app (`whisperx.service`) starts automatically; the background worker starts
  because `S3_BUCKET` is set in `/etc/whisperx.env`.

### End-to-end flow

1. `POST /api/media {"filename":"clip.mp4"}` → returns `media_id` + presigned `upload_url`.
2. `PUT` the file bytes to `upload_url` (direct to S3).
3. `POST /api/media/{id}/complete` → enqueues transcription.
4. Worker transcribes (auto-detects source language) → source subtitle track (SRT+VTT in S3).
5. `POST /api/media/{id}/subtitles {"target_languages":["ja","fr"]}` → queues translation.
6. `GET /api/media/{id}/subtitles` → per-language status + download URLs.

## Operations

- **Stop the cost** when idle: `aws --profile temp-account ec2 stop-instances --instance-ids <id>`
  (public IP changes on restart; re-check via `describe-instances`).
- **Read setup log** (if SSM is online): `AWS-RunShellScript` → `tail /var/log/whisperx-setup.log`.
- **App logs**: `journalctl -u whisperx` on the instance.

## Files

| File | Purpose |
|---|---|
| `config.env` | all tunables + derived resource names |
| `deploy.sh` | idempotent provisioning + app upload + instance launch |
| `teardown.sh` | full cleanup (reads `deploy-state.env`) |
| `user-data.sh.tmpl` | EC2 bootstrap (installs deps, runs the app as systemd) |
| `iam-trust-policy.json` | EC2 assume-role trust |
| `iam-permissions-policy.json.tmpl` | least-privilege S3 + Bedrock policy |
| `deploy-state.env` | **generated** — live resource ids for teardown |
