from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and scheduler
    from app.models.database import init_db

    init_db()

    # Scheduler is optional — only start if transcription engine is available
    # To enable: set SCHEDULER_CRON env var (e.g. "0 2 * * *" for daily at 2am)
    import os

    cron = os.environ.get("SCHEDULER_CRON")
    scheduler = None
    if cron:
        try:
            from app.models.database import SessionLocal
            from app.services.batch_processor import BatchProcessor
            from app.services.scheduler import TranscriptionScheduler
            from app.services.transcription_engine import TranscriptionEngine

            db = SessionLocal()
            engine = TranscriptionEngine()
            processor = BatchProcessor(db, engine)
            scheduler = TranscriptionScheduler(cron, processor)
            scheduler.start()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Failed to start scheduler")

    yield

    # Shutdown
    if scheduler is not None:
        scheduler.stop()


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

from app.routers import audio, transcription

app.include_router(audio.router, prefix="/api")
app.include_router(transcription.router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}
