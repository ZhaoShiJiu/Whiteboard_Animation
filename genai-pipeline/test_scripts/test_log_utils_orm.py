"""
Test that log_utils DBLogHandler correctly writes to the run_logs table via ORM.

Usage::

    cd genai-pipeline
    python test_scripts/test_log_utils_orm.py
"""

import sys
import os
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from ai_gateway.db.connection import init_db, get_session
from ai_gateway.db.models import RunLog

with open(
    Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml",
    "r", encoding="utf-8",
) as f:
    config = yaml.safe_load(f)
init_db(config["database"], run_migrations=False)

from log_utils import setup_logging, teardown_logging, ContextLogger

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
# 1. DBLogHandler writes logs to run_logs via ORM
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. DBLogHandler writes to run_logs ---")

test_run_id = "test_log_" + uuid.uuid4().hex[:8]
test_output_dir = os.path.join(
    os.path.dirname(__file__), "..", "test_output", test_run_id
)

logger = setup_logging(
    run_id=test_run_id,
    output_dir=test_output_dir,
    log_level="DEBUG",
    enable_db=True,
    db_path=str(Path(__file__).resolve().parent.parent / "ai_gateway" / "ai_gateway.db"),
)
check("setup_logging() returns ContextLogger", isinstance(logger, ContextLogger))

# Write some logs at different levels
logger.info("Test info message", extra={"key1": "value1"})
logger.debug("Test debug message")
logger.warning("Test warning message", extra={"code": 42})
logger.error("Test error message")

# Give the DB background thread time to flush
time.sleep(3.5)

# Verify logs in DB
with get_session() as s:
    logs = (
        s.query(RunLog)
        .filter(RunLog.run_id == test_run_id)
        .order_by(RunLog.id)
        .all()
    )
    check("Logs written to DB", len(logs) >= 4)

    levels = [r.level for r in logs]
    check("INFO logs present", "INFO" in levels)
    check("DEBUG logs present", "DEBUG" in levels)
    check("WARNING logs present", "WARNING" in levels)
    check("ERROR logs present", "ERROR" in levels)

    info_log = next((r for r in logs if r.level == "INFO"), None)
    if info_log:
        check("INFO message correct", info_log.message == "Test info message")
        import json
        try:
            extra = json.loads(info_log.extra_json)
            check("INFO extra_json parseable", extra.get("key1") == "value1")
        except (json.JSONDecodeError, TypeError):
            check("INFO extra_json parseable", False, "JSON decode failed")

    warn_log = next((r for r in logs if r.level == "WARNING"), None)
    if warn_log:
        check("WARNING message correct", warn_log.message == "Test warning message")

    err_log = next((r for r in logs if r.level == "ERROR"), None)
    if err_log:
        check("ERROR message correct", err_log.message == "Test error message")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. ContextLogger.bind() creates child loggers with scene context
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. ContextLogger.bind() ---")

scene_logger = logger.bind(scene_id=1, step_tag="research")
scene_logger.info("Research started")

scene_logger2 = logger.bind(scene_id=2, step_tag="image_gen")
scene_logger2.info("Image generation started")

# Flush
time.sleep(3.0)

with get_session() as s:
    scene1_logs = (
        s.query(RunLog)
        .filter(RunLog.run_id == test_run_id, RunLog.scene_id == 1)
        .all()
    )
    check("Scene 1 has log entries", len(scene1_logs) >= 1)
    if scene1_logs:
        check("Scene 1 step_tag is research",
              any(r.step_tag == "research" for r in scene1_logs))

    scene2_logs = (
        s.query(RunLog)
        .filter(RunLog.run_id == test_run_id, RunLog.scene_id == 2)
        .all()
    )
    check("Scene 2 has log entries", len(scene2_logs) >= 1)
    if scene2_logs:
        check("Scene 2 step_tag is image_gen",
              any(r.step_tag == "image_gen" for r in scene2_logs))

# ═══════════════════════════════════════════════════════════════════════════════
# 3. teardown_logging flushes remaining entries
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. teardown_logging() ---")

logger.info("Final message before teardown")
teardown_logging(test_run_id)
time.sleep(1.0)  # extra time for final flush

with get_session() as s:
    final_logs = (
        s.query(RunLog)
        .filter(RunLog.run_id == test_run_id)
        .all()
    )
    check("Final log entries persisted after teardown", len(final_logs) >= 1)

# ═══════════════════════════════════════════════════════════════════════════════
# 4. get_run_logger retrieves existing logger
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. get_run_logger() ---")

from log_utils import get_run_logger

# After teardown, should be None
found = get_run_logger(test_run_id)
check("get_run_logger returns None after teardown", found is None)

# ═══════════════════════════════════════════════════════════════════════════════
# 5. DBLogHandler fallback — DB unavailable
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. DBLogHandler fallback ---")

fallback_run_id = "test_log_fallback"
fallback_logger = setup_logging(
    run_id=fallback_run_id,
    output_dir=test_output_dir,
    log_level="INFO",
    enable_db=True,
    db_path="/nonexistent/path/to/db.sqlite",  # invalid path
)

# Should not crash — just won't write to DB
fallback_logger.info("This should not crash even though DB path is invalid")
teardown_logging(fallback_run_id)
check("DBLogHandler survives invalid DB path", True)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Log entry structure validation
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. Log entry structure ---")

with get_session() as s:
    sample = (
        s.query(RunLog)
        .filter(RunLog.run_id == test_run_id)
        .first()
    )
    if sample:
        check("Log has id (auto-increment)", sample.id is not None and sample.id > 0)
        check("Log has run_id", sample.run_id is not None)
        check("Log has level", sample.level is not None)
        check("Log has message", sample.message is not None)
        check("Log has created_at", sample.created_at is not None)
        check("Log loc is set", sample.loc is not None)

# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════════════════════
with get_session() as s:
    s.query(RunLog).filter(RunLog.run_id == test_run_id).delete()
    s.query(RunLog).filter(RunLog.run_id == fallback_run_id).delete()

# Clean up test output dirs
import shutil
for rid in [test_run_id, fallback_run_id]:
    d = os.path.join(test_output_dir, "")
    if os.path.isdir(d) and rid in d:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
