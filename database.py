import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BITECRAFT_DB", BASE_DIR / "recipes.db")).resolve()

engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Ги креира табелите ако не постојат."""
    Base.metadata.create_all(engine)
