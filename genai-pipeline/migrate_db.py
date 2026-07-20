"""
Pre-start database migration — run once before Flask / the pipeline starts.

This is the ONLY place that calls init_db(run_migrations=True), ensuring
Alembic runs with a single engine before any other thread touches the DB.

Usage:
    python genai-pipeline/migrate_db.py          # local dev
    python migrate_db.py                          # inside Docker (/app)
"""

import sys
from pathlib import Path

# Ensure genai-pipeline is importable
_project = Path(__file__).resolve().parent
if str(_project) not in sys.path:
    sys.path.insert(0, str(_project))

import yaml

from ai_gateway.db.connection import init_db

_gw_yaml = _project / "ai_gateway" / "gateway.yaml"
with open(_gw_yaml, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

print("Running database migrations...")
init_db(config["database"], run_migrations=True)
print("Database migration complete.")
