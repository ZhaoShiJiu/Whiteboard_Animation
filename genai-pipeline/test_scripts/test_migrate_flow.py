"""
Validate the fixed Docker startup flow:

1. migrate_db.py runs Alembic SUCCESSFULLY (not "Falling back to create_all")
2. After migrate_db, global engine is set
3. Calling init_db again (as Gateway would) is idempotent and instant
4. The full sequence runs without hanging

Usage::

    docker compose exec whiteboard-animation-ai python genai-pipeline/test_scripts/test_migrate_flow.py
"""

import sys
import os
import time
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import logging

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
# 1. Simulate migrate_db.py — run Alembic with a fresh engine
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. migrate_db.py equivalent ---")

from ai_gateway.db.connection import _engine as _global_engine

# Record whether engine was already initialised
was_init = _global_engine is not None
print(f"    Global engine already initialised: {was_init}")

# Import migrate_db as if we were running it at Docker startup
_gw_yaml = Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml"
with open(_gw_yaml, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# Capture Alembic log output
alembic_log_stream = io.StringIO()
alembic_handler = logging.StreamHandler(alembic_log_stream)
alembic_handler.setLevel(logging.INFO)
alembic_logger = logging.getLogger("alembic")
alembic_logger.addHandler(alembic_handler)
alembic_logger.setLevel(logging.INFO)

from ai_gateway.db.connection import init_db

t0 = time.perf_counter()
engine_m = init_db(config["database"], run_migrations=True)
t_migrate = time.perf_counter() - t0

alembic_logger.removeHandler(alembic_handler)
alembic_output = alembic_log_stream.getvalue()

check("migrate_db init succeeds (no hang)", t_migrate < 10.0,
      f"took {t_migrate:.1f}s — may have hung!")
check("migrate_db returns engine", engine_m is not None)
check("migrate_db completes in reasonable time", t_migrate < 5.0,
      f"took {t_migrate:.1f}s")

# Check that Alembic actually ran, not just "Falling back to create_all"
alembic_ok = "Alembic migrations complete" in alembic_output
alembic_fallback = "Falling back to create_all" in alembic_output
check("Alembic ran successfully (not fallback)",
      alembic_ok and not alembic_fallback,
      f"Alembic OK: {alembic_ok}, fallback: {alembic_fallback}")

print(f"    Alembic output: {alembic_output.strip()[:200]}")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Verify global engine state after migration
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. Post-migration engine state ---")

import ai_gateway.db.connection as _conn

check("Global engine is set", _conn._engine is not None)
check("SessionLocal is set", _conn._SessionLocal is not None)

# get_session() should work now
from ai_gateway.db.connection import get_session as _gs
try:
    with _gs() as s:
        from sqlalchemy import text
        s.execute(text("SELECT 1"))
    check("get_session() works after migration", True)
except Exception as exc:
    check("get_session() works after migration", False, str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Simulate Gateway init — idempotent, no second Alembic
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. Simulate Gateway init (idempotent) ---")

engine_before = _conn._engine

alembic_log_stream2 = io.StringIO()
alembic_handler2 = logging.StreamHandler(alembic_log_stream2)
alembic_handler2.setLevel(logging.INFO)
alembic_logger.addHandler(alembic_handler2)

t0 = time.perf_counter()
engine_gw = init_db(config["database"])  # default: run_migrations=False
t_gw = time.perf_counter() - t0

alembic_logger.removeHandler(alembic_handler2)
alembic_output2 = alembic_log_stream2.getvalue()

check("Gateway init_db is idempotent (same engine)", engine_gw is engine_before)
check("Gateway init_db is near-instant", t_gw < 0.5,
      f"took {t_gw * 1000:.1f} ms")
check("Gateway init does NOT run Alembic",
      "Alembic" not in alembic_output2 and "Falling back" not in alembic_output2)

# ═══════════════════════════════════════════════════════════════════════════════
# 4. db_utils works seamlessly through the entire flow
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. db_utils through the flow ---")

import uuid
from tools import db_utils as _db

# Simulate web_app POST /api/jobs (after migration, after Gateway init)
job_id = "migflow_" + uuid.uuid4().hex[:6]
_db.create_job(job_id, context="Migration flow test", language="english")
job = _db.get_job(job_id)
check("create_job + get_job after full init", job is not None and job["id"] == job_id)

_db.update_job(job_id, status="running", progress=50, message="Processing...")
job = _db.get_job(job_id)
check("update_job after full init", job["status"] == "running" and job["progress"] == 50)

# Simulate pipeline logging
test_run_id = "migflow_run"
_db.create_run(run_id=test_run_id, job_id=job_id, context=job["context"],
               output_dir="/tmp/test")
check("create_run after full init", True)

scene_id = _db.create_scene(run_id=test_run_id, scene_index=1,
                             narration="Test narration")
check("create_scene after full init", scene_id > 0)

_db.update_scene(scene_id, image_prompt="A test prompt", status="done", cost=0.05)
check("update_scene after full init", True)

_db.update_run(test_run_id, status="completed", scene_count=1, cost_total=0.05,
               final_video="/tmp/test/final.mp4")
check("update_run after full init", True)

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cleanup
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. Cleanup ---")

from ai_gateway.db.models import Job, Run, Scene

with _gs() as s:
    s.query(Scene).filter(Scene.run_id == test_run_id).delete()
    s.query(Run).filter(Run.id == test_run_id).delete()
    j = s.get(Job, job_id)
    if j:
        s.delete(j)
check("Cleanup completed", True)

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
