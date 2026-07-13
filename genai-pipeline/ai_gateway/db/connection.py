"""
Database connection — SQLite for zero-config development.
Switch to PostgreSQL by changing the connection string in gateway.yaml.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def init_db(config: dict) -> Engine:
    """
    Initialise the database engine and create all tables.

    Args:
        config: The ``database`` section from gateway.yaml.

    Returns:
        The SQLAlchemy Engine instance.
    """
    global _engine, _SessionLocal

    db_type = config.get("type", "sqlite")

    if db_type == "sqlite":
        db_path = config.get("path", "ai_gateway.db")
        # Resolve relative to the ai_gateway directory
        if not os.path.isabs(db_path):
            db_path = str(Path(__file__).parent.parent / db_path)
        connect_url = f"sqlite:///{db_path}"
    elif db_type == "postgresql":
        connect_url = config.get("url", "")
        if not connect_url:
            raise ValueError("database.url is required when type=postgresql")
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    _engine = create_engine(connect_url, echo=False)
    _SessionLocal = sessionmaker(bind=_engine)

    # Auto-create tables
    Base.metadata.create_all(_engine)

    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    """Yields a SQLAlchemy Session. Commits on success, rolls back on error."""
    if _SessionLocal is None:
        raise RuntimeError(
            "Database not initialised. Call init_db(config) first."
        )
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
