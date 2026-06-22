import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and (optionally) the subtitle worker.
    import logging
    import os

    from app.models.database import init_db

    init_db()

    # The background subtitle worker runs only when storage is configured
    # (S3_BUCKET set). This keeps it off in local/test imports. It loads the
    # WhisperX model once and drives transcription + Bedrock translation.
    worker = None
    if os.environ.get("S3_BUCKET"):
        try:
            from app.routers.transcription import _get_engine
            from app.services.storage import StorageService
            from app.services.translation_engine import TranslationEngine
            from app.services.worker import SubtitleWorker

            worker = SubtitleWorker(
                transcription_engine=_get_engine(),
                translation_engine=TranslationEngine.from_env(),
                storage=StorageService.from_env(),
            )
            worker.start()
        except Exception:
            logging.getLogger(__name__).exception("Failed to start subtitle worker")

    yield

    # Shutdown
    if worker is not None:
        worker.stop()


app = FastAPI(
    title="批量语音转文本平台",
    description="Batch Speech-to-Text Platform powered by WhisperX",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: when an explicit origin is configured (the CloudFront domain), use it
# with credentials; otherwise fall back to permissive (local dev). The
# "*" + allow_credentials=True combination is invalid per the CORS spec, so we
# only enable credentials when a concrete origin is set.
_cors_origin = os.environ.get("CORS_ALLOW_ORIGIN")
if _cors_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_cors_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Origin lock: when ORIGIN_SECRET is set, every request (except liveness) must
# carry a matching X-Origin-Secret header. CloudFront injects it as a custom
# origin header, so requests that bypass CloudFront and hit the instance
# directly are rejected with 403. This is the second layer alongside the
# security group (which only allows CloudFront's shared origin-facing prefix
# list — necessary but not sufficient on its own).
_ORIGIN_SECRET = os.environ.get("ORIGIN_SECRET")
_ORIGIN_EXEMPT_PATHS = {"/health"}


@app.middleware("http")
async def _enforce_origin_secret(request: Request, call_next):
    if _ORIGIN_SECRET and request.url.path not in _ORIGIN_EXEMPT_PATHS:
        if request.headers.get("x-origin-secret") != _ORIGIN_SECRET:
            return JSONResponse(status_code=403, content={"detail": "forbidden"})
    return await call_next(request)


from app.routers import audio, media, transcription

app.include_router(audio.router, prefix="/api")
app.include_router(transcription.router, prefix="/api")
app.include_router(media.router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Serve the built frontend SPA (if present). FRONTEND_DIST points at the Vite
# build output. The catch-all returns index.html for client-side routes, but
# /api/* and /health are matched above and never reach here.
_FRONTEND_DIST = os.environ.get("FRONTEND_DIST", "/opt/whisperx/frontend")
if os.path.isdir(_FRONTEND_DIST):
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # Hashed assets under /assets are served directly.
    _assets = os.path.join(_FRONTEND_DIST, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    _index = os.path.join(_FRONTEND_DIST, "index.html")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # Don't hijack API/health (already routed) — only serve the SPA shell.
        candidate = os.path.join(_FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(_index)
