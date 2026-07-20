"""
Database connection — SQLite for zero-config development.
Switch to PostgreSQL by changing the connection string in gateway.yaml.

Provides:
- ``init_db()``: initialise engine, run Alembic migrations, create tables
- ``get_session()``: context manager yielding a SQLAlchemy Session
- ``get_engine()``: return the current Engine (for Alembic / direct use)
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_logger = logging.getLogger("ai_gateway.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    """Return the current SQLAlchemy Engine.  Raises if not initialised."""
    if _engine is None:
        raise RuntimeError(
            "Database not initialised. Call init_db(config) first."
        )
    return _engine


def init_db(config: dict, run_migrations: bool = False) -> Engine:
    """
    Initialise the database engine and create all tables.

    **Idempotent** — if the global engine is already initialised, returns it
    immediately without re-running migrations or ``create_all``.  This prevents
    lock contention when multiple code paths (web app, pipeline thread,
    Gateway singleton) trigger initialisation concurrently.

    Args:
        config: The ``database`` section from gateway.yaml.
        run_migrations: If True, run ``alembic upgrade head`` before
            ``create_all``.  Set to True on the *first* initialisation in a
            process (e.g. from ``Gateway.__init__``).  Defaults to False to
            avoid SQLite lock hangs in multi-engine Docker scenarios.

    Returns:
        The SQLAlchemy Engine instance.
    """
    global _engine, _SessionLocal

    # -- Idempotent: return existing engine if already set up ------------------
    if _engine is not None:
        _logger.debug("Database already initialised — returning existing engine.")
        return _engine

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

    # -- Run Alembic migrations (if enabled) -----------------------------------
    if run_migrations:
        try:
            _run_alembic_migrations()
        except Exception as exc:
            _logger.warning(
                "Alembic migrations failed (%s). Falling back to create_all.", exc
            )

    # -- Auto-create any tables not yet covered by migrations -------------------
    Base.metadata.create_all(_engine)

    return _engine


def _run_alembic_migrations() -> None:
    """Run ``alembic upgrade head`` programmatically."""
    from alembic.config import Config as AlembicConfig
    from alembic.command import upgrade

    # Path to alembic.ini (next to this connection.py)
    alembic_ini = Path(__file__).parent / "alembic.ini"
    if not alembic_ini.exists():
        _logger.debug("No alembic.ini found — skipping migrations.")
        return

    alembic_cfg = AlembicConfig(str(alembic_ini))
    # Prevent Alembic from parsing command-line args (it would fail inside a
    # web server or background thread).
    alembic_cfg.cmd_opts = type("_", (), {"x": None})()
    upgrade(alembic_cfg, "head")
    _logger.info("Alembic migrations complete.")


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
