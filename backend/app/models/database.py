import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# DATABASE_URL is overridable via env (deployment points it at an absolute path
# that exists; default keeps the local relative path for dev/tests).
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/speech_to_text.db")

# For a local SQLite file path, ensure the parent directory exists so the
# engine can create the file (avoids "unable to open database file" at startup).
if DATABASE_URL.startswith("sqlite:///") and not DATABASE_URL.startswith("sqlite:////"):
    _rel = DATABASE_URL.replace("sqlite:///", "", 1)
    _dirname = os.path.dirname(_rel)
    if _dirname:
        os.makedirs(_dirname, exist_ok=True)
elif DATABASE_URL.startswith("sqlite:////"):
    _abs = "/" + DATABASE_URL.replace("sqlite:////", "", 1)
    os.makedirs(os.path.dirname(_abs), exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
