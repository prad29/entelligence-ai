from sqlmodel import create_engine, Session, SQLModel
from typing import Generator

from app.config import settings

engine = create_engine(settings.DATABASE_URL, echo=False)


def create_db_and_tables() -> None:
    """Create all tables (called during startup in Phase 2+)."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session."""
    with Session(engine) as session:
        yield session
