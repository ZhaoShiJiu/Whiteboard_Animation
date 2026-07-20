"""
Docker integration smoke test — validates that the full stack works inside a
Docker container without making any external AI calls.

Run INSIDE the container::

    docker compose exec whiteboard-animation-ai python genai-pipeline/test_scripts/test_docker_integration.py

Or as a one-shot::

    docker compose run --rm whiteboard-animation-ai python genai-pipeline/test_scripts/test_docker_integration.py
"""

import sys
import os
import json
import time
import uuid
from pathlib import Path

# Ensure genai-pipeline is importable (should already be the case inside Docker
# because /app/genai-pipeline is on sys.path via the CMD working dir, but be
# defensive).
_project = Path(__file__).resolve().parent.parent
if str(_project) not in sys.path:
    sys.path.insert(0, str(_project))

_passed = 0
_failed = 0


def check(desc: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {desc}")
    else:
        _failed += 1
        print(f"  [FAIL] {desc}  — {detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# 0. Environment sanity checks
# ═══════════════════════════════════════════════════════════════════════════════
print("--- 0. Environment ---")

check("Python >= 3.11",
      sys.version_info >= (3, 11),
      f"Python {sys.version}")

# Check critical env vars
_output_dir = os.environ.get("OUTPUT_DIR", "")
check("OUTPUT_DIR is set", bool(_output_dir),
      f"OUTPUT_DIR={_output_dir}")

# Check that the gateway.yaml is reachable
_gw_yaml = _project / "ai_gateway" / "gateway.yaml"
check("gateway.yaml exists", _gw_yaml.exists(),
      f"Expected at {_gw_yaml}")

# Check that alembic is installed
try:
    import alembic
    check("alembic package installed", True,
          f"alembic {alembic.__version__}")
except ImportError:
    check("alembic package installed", False,
          "pip install alembic>=1.13.0")

# Check alembic.ini
_alembic_ini = _project / "ai_gateway" / "db" / "alembic.ini"
check("alembic.ini exists", _alembic_ini.exists(),
      f"Expected at {_alembic_ini}")

# Check migrations directory
_migrations = _project / "ai_gateway" / "db" / "migrations" / "versions"
check("migrations/versions exists", _migrations.is_dir(),
      f"Expected at {_migrations}")

# Check numpy (needed for image_library cosine similarity)
try:
    import numpy
    check("numpy installed", True,
          f"numpy {numpy.__version__}")
except ImportError:
    check("numpy installed", False,
          "pip install numpy")

# Pillow (for thumbnails)
try:
    import PIL
    check("Pillow installed", True,
          f"Pillow {PIL.__version__}")
except ImportError:
    check("Pillow installed", False,
          "pip install Pillow")

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Database initialisation & migration
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. Database initialisation ---")

import yaml
with open(_gw_yaml, "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

_db_cfg = _config.get("database", {})
check("DB config present", bool(_db_cfg),
      f"type={_db_cfg.get('type')}, path={_db_cfg.get('path')}")

from ai_gateway.db.connection import init_db, get_session, get_engine

# This is what Gateway.__init__ does in production — runs Alembic then create_all
engine = init_db(_db_cfg, run_migrations=True)
check("init_db() returns engine", engine is not None)

from ai_gateway.db.models import (
    AiRequestLog, AiUsage, RunLog,
    Job, Run, Scene, MediaAsset, ImageLibrary,
)

# Verify all expected tables exist
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
expected_tables = {
    "ai_request_logs", "ai_usage", "run_logs",
    "jobs", "runs", "scenes", "media_assets", "image_library",
    "alembic_version",
}
missing = expected_tables - set(tables)
check("All expected tables created",
      len(missing) == 0,
      f"Missing: {missing}" if missing else "All present")

for tbl in sorted(expected_tables):
    check(f"  Table '{tbl}' exists", tbl in tables)

# Check that existing data (ai_request_logs, ai_usage) was preserved
check("Alembic did not drop existing tables", True)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Session & threading safety
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. Session & threading ---")

import threading

errors_in_threads = []


def _thread_worker(worker_id: int):
    try:
        with get_session() as s:
            job = Job(
                id=f"thread_test_{worker_id}_{uuid.uuid4().hex[:4]}",
                status="queued",
                context=f"Worker {worker_id}",
                language="english",
            )
            s.add(job)
    except Exception as exc:
        errors_in_threads.append(f"Worker {worker_id}: {exc}")


threads = [threading.Thread(target=_thread_worker, args=(i,)) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("3 threads wrote to DB without errors",
      len(errors_in_threads) == 0,
      "; ".join(errors_in_threads) if errors_in_threads else "")

# Cleanup thread test jobs
with get_session() as s:
    from sqlalchemy import delete
    s.execute(delete(Job).where(Job.id.like("thread_test_%")))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. db_utils helpers (the code path used by web_app)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. db_utils path (web_app code path) ---")

from tools import db_utils as _db

# Simulate what POST /api/jobs does
job_id = uuid.uuid4().hex[:12]
_db.create_job(job_id, context="Docker smoke test", language="english",
               settings={"fast_mode": False, "research_mode": "web"})
check("create_job() in Docker", True)

job = _db.get_job(job_id)
check("get_job() returns dict", isinstance(job, dict) and job["id"] == job_id)

jobs = _db.list_jobs()
check("list_jobs() returns list", isinstance(jobs, list))

# Simulate cost endpoint
costs = _db.get_cost_summary()
check("get_cost_summary() returns dict", isinstance(costs, dict))
check("total_cost in response", "total_cost" in costs)

# Cleanup
_db.update_job(job_id, status="cancelled")
with get_session() as s:
    j = s.get(Job, job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Logging -> DB (the DBLogHandler ORM path)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. DBLogHandler -> ORM ---")

from log_utils import setup_logging, teardown_logging

test_run_id = "docker_test_" + uuid.uuid4().hex[:6]
_log_out = os.path.join(_output_dir, test_run_id) if _output_dir else None
if _log_out:
    os.makedirs(_log_out, exist_ok=True)

_db_path = str(_project / "ai_gateway" / "ai_gateway.db")
pipeline_logger = setup_logging(
    run_id=test_run_id,
    output_dir=_log_out,
    log_level="INFO",
    enable_db=True,
    db_path=_db_path,
)

pipeline_logger.info("Docker integration test log 1")
pipeline_logger.info("Docker integration test log 2", extra={"docker": True})
pipeline_logger.warning("Docker integration test warning")

# Allow DBLogHandler background thread to flush
time.sleep(3.5)

with get_session() as s:
    count = s.query(RunLog).filter(RunLog.run_id == test_run_id).count()
    check("DBLogHandler wrote logs to run_logs table", count >= 3,
          f"Found {count} entries, expected >= 3")

teardown_logging(test_run_id)

# Cleanup
with get_session() as s:
    s.query(RunLog).filter(RunLog.run_id == test_run_id).delete()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Image library (without API calls — local-only operations)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. Image library (local ops) ---")

import hashlib
import struct
import zlib


def _make_test_png(w=2, h=2) -> bytes:
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    hdr = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    raw = b"\x00" + b"\xff\x00\x00" * w
    raw *= h
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return hdr + ihdr + idat + iend


from tools.image_library import (
    process_and_store_image,
    retrieve_best_match,
    get_image_bytes,
    _make_thumbnail,
    _get_dimensions,
    _cosine_similarity,
)

png = _make_test_png(64, 36)
img_id = process_and_store_image(
    image_bytes=png,
    prompt="Docker test image",
    scene_desc="Used for docker integration test",
    source_run_id="docker_test",
)
check("process_and_store_image() in Docker", img_id > 0)

# Dedup test
img_id2 = process_and_store_image(
    image_bytes=bytes(png),
    prompt="Docker test image (duplicate content)",
)
check("Deduplication works in Docker", img_id2 == img_id)

# get_image_bytes
retrieved = get_image_bytes(img_id)
check("get_image_bytes() works in Docker", retrieved == png)

# Thumbnail
thumb = _make_thumbnail(png)
check("_make_thumbnail() works in Docker", thumb is not None and thumb[:2] == b"\xff\xd8")

# Dimensions
w, h = _get_dimensions(png)
check("_get_dimensions() works in Docker", w == 64 and h == 36)

# Cosine similarity
import numpy as np
a = np.array([1.0, 0.0], dtype=np.float32)
b = np.array([0.0, 1.0], dtype=np.float32)
sim = _cosine_similarity(a, b)
check("_cosine_similarity() works in Docker", abs(sim) < 1e-6)

# Cleanup
with get_session() as s:
    s.delete(s.get(ImageLibrary, img_id))

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Web app route registration
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. Web app routes ---")

from web_app.app import app

expected_routes = [
    "/api/health",
    "/api/jobs",
    "/api/outputs",
    "/api/costs",
    "/api/logs/<run_id>",
    "/api/logs/stats",
    "/api/images",
    "/api/images/<int:image_id>/thumbnail",
]

registered_rules = {r.rule for r in app.url_map.iter_rules()}
for route in expected_routes:
    check(f"Route registered: {route}",
          route in registered_rules)

# Quick test with Flask test client
client = app.test_client()
resp = client.get("/api/health")
check("Flask test client: GET /api/health → 200",
      resp.status_code == 200)

resp = client.get("/api/costs")
check("Flask test client: GET /api/costs → 200",
      resp.status_code == 200)

resp = client.get("/api/images")
check("Flask test client: GET /api/images → 200",
      resp.status_code == 200)

# ═══════════════════════════════════════════════════════════════════════════════
# 7. File system paths (Docker-specific)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 7. File system paths ---")

_paths_to_check = [
    ("Project root (genai-pipeline)", _project),
    ("ai_gateway/", _project / "ai_gateway"),
    ("ai_gateway/gateway.yaml", _project / "ai_gateway" / "gateway.yaml"),
    ("ai_gateway/db/", _project / "ai_gateway" / "db"),
    ("ai_gateway/db/alembic.ini", _project / "ai_gateway" / "db" / "alembic.ini"),
    ("ai_gateway/db/migrations/", _project / "ai_gateway" / "db" / "migrations"),
    ("tools/", _project / "tools"),
    ("tools/db_utils.py", _project / "tools" / "db_utils.py"),
    ("tools/image_library.py", _project / "tools" / "image_library.py"),
    ("tools/embedding_utils.py", _project / "tools" / "embedding_utils.py"),
    ("web_app/", _project.parent / "web_app"),
    ("web_app/app.py", _project.parent / "web_app" / "app.py"),
]

for label, path in _paths_to_check:
    check(f"Path exists: {label}", path.exists(),
          f"Expected: {path}")

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
    sys.exit(1)
else:
    print("Docker integration smoke test PASSED!")
    sys.exit(0)
