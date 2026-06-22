from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import audio, media, transcription

app.include_router(audio.router, prefix="/api")
app.include_router(transcription.router, prefix="/api")
app.include_router(media.router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}
