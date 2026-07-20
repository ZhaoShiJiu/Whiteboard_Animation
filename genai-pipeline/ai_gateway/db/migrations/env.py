"""
Alembic migration environment for Whiteboard Animation AI.

Reads database connection info from gateway.yaml and auto-discovers
all ORM models registered on the shared ``Base`` metadata.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool

# -- Ensure the ai_gateway package is importable --------------------------------
# Alembic may be invoked from any directory; make sure the parent of ai_gateway
# is on sys.path so that ``from ai_gateway.db.models import Base`` works.
_ai_gateway_parent = str(Path(__file__).resolve().parent.parent.parent.parent)
if _ai_gateway_parent not in sys.path:
    sys.path.insert(0, _ai_gateway_parent)

# -- Alembic Config object ------------------------------------------------------
config = context.config

# -- Logging --------------------------------------------------------------------
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# -- Import all models so Base.metadata knows about every table -----------------
from ai_gateway.db.models import Base  # noqa: E402

target_metadata = Base.metadata

# -- Resolve database URL from gateway.yaml -------------------------------------
# We replicate the logic from connection.py so that the same sqlite path
# resolution is used regardless of where Alembic is invoked from.


def _get_database_url() -> str:
    """Build a SQLAlchemy URL from the gateway.yaml database section."""
    import yaml

    gateway_yaml = Path(__file__).resolve().parent.parent.parent / "gateway.yaml"
    if gateway_yaml.exists():
        with open(gateway_yaml, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        db_cfg = raw.get("database", {})
    else:
        db_cfg = {}

    db_type = db_cfg.get("type", "sqlite")
    if db_type == "sqlite":
        db_path = db_cfg.get("path", "ai_gateway.db")
        if not os.path.isabs(db_path):
            # Resolve relative to the ai_gateway directory
            db_path = str(Path(__file__).resolve().parent.parent.parent / db_path)
        return f"sqlite:///{db_path}"
    elif db_type == "postgresql":
        url = db_cfg.get("url", "")
        if not url:
            raise ValueError("database.url is required when type=postgresql")
        return url
    else:
        raise ValueError(f"Unsupported database type: {db_type}")


# Inject the resolved URL into the Alembic config so that offline mode works.
config.set_main_option("sqlalchemy.url", _get_database_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well.  By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Include existing tables so that --autogenerate does not try to
        # re-create them.
        render_as_batch=True,  # SQLite-friendly ALTER support
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Uses our own URL resolution (from gateway.yaml) instead of relying on
    ``engine_from_config``, because the alembic.ini may not contain a valid URL.
    """
    from sqlalchemy import create_engine

    url = _get_database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly ALTER support
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
