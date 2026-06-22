# Deployment — WhisperX Subtitle Platform

Deploys the video-subtitling backend to the **temp-account** AWS account only.
Every script asserts the account id (`ACCOUNT_ID_REDACTED`) and refuses to run elsewhere.

## Architecture (front door)

```
viewer ──HTTPS──▶ CloudFront ──HTTP + X-Origin-Secret──▶ EC2 (FastAPI :8000)
                  (only entry)                            └ worker + WhisperX
                                                          EC2 SG: ingress :8000
                                                          only from CloudFront's
                                                          managed prefix list
```

Two independent controls stop anyone from bypassing CloudFront and hitting the
instance directly — **both required**:
1. **Security group** allows `:8000` only from CloudFront's managed origin-facing
   prefix list (`pl-3b927c52`). Direct public TCP is dropped.
2. **Origin secret**: CloudFront injects `X-Origin-Secret`; the app 403s requests
   without it (the prefix list is shared across all CloudFront customers, so the
   SG alone is necessary-but-not-sufficient).

> ⚠️ With no custom domain, viewer→CloudFront is HTTPS but CloudFront→origin is
> HTTP. End-to-end TLS needs a domain + ACM cert (+ ideally an ALB). Acceptable
> for a temp demo box; not the final production posture.

## What gets created (all in `temp-account`, us-east-1)

| Resource | Name | Purpose | Cost |
|---|---|---|---|
| S3 bucket | `whisperx-subs-ACCOUNT_ID_REDACTED-us-east-1` | source videos + generated SRT/VTT + app tarball | ~$0.023/GB-mo |
| IAM role + instance profile | `whisperx-subs-ec2-role` / `-profile` | scoped S3 + `bedrock:InvokeModel` (Haiku) | $0 |
| Security group | `whisperx-subs-sg` | `:8000` from CloudFront prefix list only | $0 |
| EC2 instance | `whisperx-subs-app` (`c7i.2xlarge`, CPU) | runs FastAPI + background worker + WhisperX | **~$0.36/hr ≈ $8.6/day** |
| CloudFront distribution | `${STACK} front door` | the only public entry point | ~$0.085/GB out + req |

> GPU quota on this workshop account is **0**, so WhisperX runs on CPU (`large-v3` int8).
> Translation uses **Claude Haiku 4.5 on Bedrock** via cross-region inference profile
> `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Opus is not available on this account).

## Usage

```bash
cd deploy
bash deploy.sh        # create bucket + IAM + SG + EC2; writes deploy-state.env
bash front-door.sh    # put CloudFront in front, push origin secret, lock the SG
bash teardown.sh      # remove everything (incl. CloudFront)
```

After `front-door.sh`, reach the app at the **CloudFront** URL printed in its
output (`https://<id>.cloudfront.net`). The instance's own IP no longer answers.

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
