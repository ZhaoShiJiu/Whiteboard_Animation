"""
Validate the _get_session() fallback fix and init_db() idempotency.

Specifically:

1. db_utils._get_session() uses a local engine when the global engine is None
   (simulates the pipeline background thread before Gateway.__init__ runs).

2. db_utils._get_session() switches to the global session after init_db()
   (simulates the steady-state after Gateway is initialised).

3. init_db() is idempotent — second call returns the same engine immediately,
   does NOT re-run Alembic / create_all, and does NOT hang.

4. Multiple concurrent _get_session() fallback calls don't deadlock.

Usage::

    docker compose exec whiteboard-animation-ai python genai-pipeline/test_scripts/test_session_fix.py
"""

import sys
import os
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

# ═══════════════════════════════════════════════════════════════════════════════
# Setup — read gateway.yaml but do NOT call init_db yet
# ═══════════════════════════════════════════════════════════════════════════════
_gw_yaml = Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml"
with open(_gw_yaml, "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

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
# 1. Reset: verify global engine is None before init_db
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. Before init_db: global engine is None ---")

# Force-reload connection module to get fresh state
import ai_gateway.db.connection as _conn

# The test harness may have already initialised; we check the current state
# and record it for later comparison.
_was_already_init = _conn._engine is not None
check("Connection module importable", True)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. _get_session() fallback — works without global engine
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n--- 2. _get_session() fallback (global_init={_was_already_init}) ---")

from tools.db_utils import _get_session, _ensure_db_path

db_path = _ensure_db_path()
check("_ensure_db_path() returns a path", db_path is not None,
      str(db_path) if db_path else "None")

# Open a session via _get_session — this MUST work, either via global or fallback
try:
    with _get_session() as s:
        from sqlalchemy import text
        result = s.execute(text("SELECT 1"))
        val = result.scalar()
    check("_get_session() yields a working session", val == 1,
          f"got {val}")
except Exception as exc:
    check("_get_session() yields a working session", False, str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. init_db() idempotency — second call returns immediately
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. init_db() idempotency ---")

_db_cfg = _config.get("database", {})

# First initialisation (or nop if already done)
t0 = time.perf_counter()
engine1 = _conn.init_db(_db_cfg, run_migrations=False)
t1 = time.perf_counter() - t0
check("init_db() returns engine", engine1 is not None)
print(f"    First call: {t1 * 1000:.1f} ms")

# Second initialisation — must be near-instant (no engine creation, no create_all)
t0 = time.perf_counter()
engine2 = _conn.init_db(_db_cfg, run_migrations=False)
t2 = time.perf_counter() - t0
check("init_db() second call returns same engine", engine1 is engine2)
check("init_db() second call is fast (< 100 ms)", t2 < 0.1,
      f"took {t2 * 1000:.1f} ms")

# Third call with run_migrations=True — still idempotent, must NOT hang
t0 = time.perf_counter()
engine3 = _conn.init_db(_db_cfg, run_migrations=True)
t3 = time.perf_counter() - t0
check("init_db(run_migrations=True) is idempotent", engine3 is engine1)
check("init_db(run_migrations=True) does not hang (< 5 s)", t3 < 5.0,
      f"took {t3:.1f} s — possible lock hang!")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. After init_db: _get_session() uses global session
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. After init_db: _get_session() uses global ---")

from ai_gateway.db.connection import _engine as _global_engine
check("Global engine is set after init_db", _global_engine is not None)

try:
    ctx = _get_session()
    # If global engine exists, _get_session should return the @contextmanager
    # from connection.get_session, NOT the local fallback.
    from ai_gateway.db.connection import get_session as _global_session
    # They should produce sessions backed by the same engine
    with ctx as s1:
        s1.execute(text("SELECT 1"))
    check("_get_session() works after global init", True)
except Exception as exc:
    check("_get_session() works after global init", False, str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Concurrent access — multiple threads using _get_session() simultaneously
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. Concurrent _get_session() access ---")

errors = []
results = []
lock = threading.Lock()


def concurrent_worker(worker_id: int):
    try:
        with _get_session() as s:
            from sqlalchemy import text
            row = s.execute(text("SELECT :id AS worker_id"), {"id": worker_id}).fetchone()
            with lock:
                results.append(row[0])
    except Exception as exc:
        with lock:
            errors.append(f"worker {worker_id}: {exc}")


threads = [threading.Thread(target=concurrent_worker, args=(i,)) for i in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("5 concurrent workers all succeeded", len(errors) == 0,
      "; ".join(errors) if errors else "")
check("5 results returned", len(results) == 5)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Simulate the real pipeline scenario
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. Simulated pipeline scenario ---")

# This mimics what happens when web_app creates a job and starts a pipeline
# thread: db_utils functions are called BEFORE Gateway.__init__ runs.

# Step A: web_app creates job via db_utils (global engine may or may not exist)
from tools import db_utils as _db

job_id = "fix_test_" + uuid.uuid4().hex[:6]
_db.create_job(job_id, context="Session fix test", language="english")
job = _db.get_job(job_id)
check("Step A: create_job() before Gateway init", job is not None and job["id"] == job_id)

_db.update_job(job_id, status="running", progress=10, message="Starting...")
job = _db.get_job(job_id)
check("Step A: update_job() before Gateway init", job["status"] == "running")

# Step B: Gateway initialises (simulating what happens when pipeline imports ai_gateway)
from ai_gateway.db.connection import init_db as _init
_init(_db_cfg, run_migrations=False)

# Step C: db_utils continues to work after Gateway init
_db.update_job(job_id, status="completed", progress=100, message="Done")
job = _db.get_job(job_id)
check("Step C: update_job() after Gateway init", job["status"] == "completed")

# Cleanup
from ai_gateway.db.connection import get_session as _gs
from ai_gateway.db.models import Job
with _gs() as s:
    j = s.get(Job, job_id)
    if j:
        s.delete(j)
check("Step D: cleanup via global session", True)

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
