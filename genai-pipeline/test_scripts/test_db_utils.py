"""
Test the tools/db_utils helper functions.

Usage::

    cd genai-pipeline
    python test_scripts/test_db_utils.py
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import datetime
import uuid

from ai_gateway.db.connection import init_db, get_session
from ai_gateway.db.models import Job, Run, Scene, MediaAsset, RunLog

with open(
    Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml",
    "r", encoding="utf-8",
) as f:
    config = yaml.safe_load(f)
init_db(config["database"], run_migrations=False)

from tools import db_utils

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
# 1. Job helpers
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. Job helpers ---")

job_id = "dbutils_" + uuid.uuid4().hex[:6]

db_utils.create_job(
    job_id=job_id,
    context="Test context for db_utils",
    language="chinese",
    settings={"fast_mode": True, "video_provider": "seedance"},
)
check("create_job() returns without error", True)

job = db_utils.get_job(job_id)
check("get_job() returns dict", isinstance(job, dict))
check("get_job() id matches", job["id"] == job_id)
check("get_job() status is queued", job["status"] == "queued")
check("get_job() context matches", job["context"] == "Test context for db_utils")
check("get_job() language matches", job["language"] == "chinese")

db_utils.update_job(job_id, status="running", progress=30, message="Working...")
job = db_utils.get_job(job_id)
check("update_job() status changed", job["status"] == "running")
check("update_job() progress changed", job["progress"] == 30)
check("update_job() message changed", job["message"] == "Working...")

jobs = db_utils.list_jobs()
check("list_jobs() returns list", isinstance(jobs, list))
check("list_jobs() contains our job", any(j["id"] == job_id for j in jobs))

# Non-existent job
null_job = db_utils.get_job("nonexistent_job_id")
check("get_job() returns None for missing job", null_job is None)

# Cleanup
with get_session() as s:
    j = s.get(Job, job_id)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Run helpers
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. Run helpers ---")

run_id = "dbutils_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

db_utils.create_run(
    run_id=run_id,
    job_id=job_id,
    context="Run context",
    language="english",
    settings={"do_research": True},
    output_dir="/tmp/output/test_run",
)
check("create_run() returns without error", True)

db_utils.update_run(
    run_id,
    scene_count=3,
    video_plan_json={"scenes": [{"index": 1}, {"index": 2}, {"index": 3}]},
    research_report="## Research\n\nFindings here.",
)
check("update_run() partial fields", True)

runs = db_utils.list_runs()
check("list_runs() returns list", isinstance(runs, list))
check("list_runs() contains our run", any(r["run_id"] == run_id for r in runs))

# Check a listed run's fields
our_run = next(r for r in runs if r["run_id"] == run_id)
check("list_runs entry has run_id", our_run["run_id"] == run_id)
check("list_runs entry has created_at", bool(our_run["created_at"]))
check("list_runs entry has status", "status" in our_run)
check("list_runs entry has scene_count", our_run["scene_count"] == 3)
check("list_runs entry has context", our_run["context"] == "Run context")
check("list_runs entry has language", our_run["language"] == "english")

# Complete the run
db_utils.update_run(
    run_id,
    status="completed",
    final_video="/tmp/output/test_run/final.mp4",
    cost_total=2.50,
    completed_at=datetime.datetime.utcnow(),
)
check("update_run() completed", True)

# Cleanup
with get_session() as s:
    r = s.get(Run, run_id)
    if r:
        s.delete(r)

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Scene helpers
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. Scene helpers ---")

tmp_run_id = "dbutils_tmp_run"
with get_session() as s:
    s.add(Run(id=tmp_run_id, status="running", context="tmp"))
    s.flush()

scene_id = db_utils.create_scene(
    run_id=tmp_run_id,
    scene_index=1,
    narration="This is a test narration.",
    description="Test description for scene 1.",
    visual_setup="Whiteboard style, clean lines",
    text_overlay="Scene One",
)
check("create_scene() returns id > 0", scene_id > 0)
check("create_scene() returns int", isinstance(scene_id, int))

db_utils.update_scene(
    scene_id,
    image_prompt="A whiteboard sketch of the solar system",
    audio_path="/tmp/audio_test.mp3",
    srt_content="1\n00:00:00,000 --> 00:00:05,000\nTest",
    status="done",
    cost=0.03,
)
check("update_scene() with pipeline fields", True)

# Verify via direct ORM
with get_session() as s:
    sc = s.get(Scene, scene_id)
    check("Scene persisted correctly", sc is not None)
    check("Scene.narration matches", sc.narration == "This is a test narration.")
    check("Scene.image_prompt matches", sc.image_prompt == "A whiteboard sketch of the solar system")
    check("Scene.status == done", sc.status == "done")
    check("Scene.cost == 0.03", sc.cost == 0.03)

# Scene with minimal fields
scene_id2 = db_utils.create_scene(
    run_id=tmp_run_id,
    scene_index=2,
)
check("create_scene() with minimal fields returns id", scene_id2 > 0)

with get_session() as s:
    sc = s.get(Scene, scene_id2)
    check("Minimal scene narration is empty", sc.narration == "")
    check("Minimal scene status is pending", sc.status == "pending")

# Cleanup
with get_session() as s:
    s.delete(s.get(Scene, scene_id))
    s.delete(s.get(Scene, scene_id2))
    s.delete(s.get(Run, tmp_run_id))

# ═══════════════════════════════════════════════════════════════════════════════
# 4. MediaAsset helpers
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. MediaAsset helpers ---")

tmp_run_id2 = "dbutils_tmp_run2"
with get_session() as s:
    s.add(Run(id=tmp_run_id2, status="running", context="tmp"))

db_utils.create_media_asset(
    run_id=tmp_run_id2,
    scene_id=1,
    asset_type="audio",
    file_path="/tmp/test_audio.mp3",
    file_name="test_audio.mp3",
    file_size=50000,
    mime_type="audio/mpeg",
    is_temporary=True,
)
check("create_media_asset() audio", True)

db_utils.create_media_asset(
    run_id=tmp_run_id2,
    asset_type="srt",
    file_path="/tmp/test.srt",
    content_text="1\n00:00:00,000 --> 00:00:05,000\nSubtitle text",
)
check("create_media_asset() with content_text", True)

db_utils.create_media_asset(
    run_id=tmp_run_id2,
    asset_type="animation",
    file_path="/tmp/test_anim.mp4",
    is_temporary=True,
)
check("create_media_asset() animation", True)

# Verify
with get_session() as s:
    assets = s.query(MediaAsset).filter(MediaAsset.run_id == tmp_run_id2).all()
    check("3 media assets created", len(assets) == 3)

    types = {a.asset_type for a in assets}
    check("audio asset present", "audio" in types)
    check("srt asset present", "srt" in types)
    check("animation asset present", "animation" in types)

# Delete temporary assets
deleted = db_utils.delete_temporary_assets(tmp_run_id2)
check("delete_temporary_assets() returns count", deleted == 2)

with get_session() as s:
    remaining = s.query(MediaAsset).filter(MediaAsset.run_id == tmp_run_id2).all()
    check("1 asset remaining after temp cleanup", len(remaining) == 1)
    check("remaining asset is srt (not temporary)", remaining[0].asset_type == "srt")

# Cleanup
with get_session() as s:
    for a in s.query(MediaAsset).filter(MediaAsset.run_id == tmp_run_id2).all():
        s.delete(a)
    s.delete(s.get(Run, tmp_run_id2))

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cost helpers
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. Cost helpers ---")

costs = db_utils.get_cost_summary()
check("get_cost_summary() returns dict", isinstance(costs, dict))
check("get_cost_summary() has total_cost", "total_cost" in costs)
check("get_cost_summary() has total_requests", "total_requests" in costs)
check("get_cost_summary() has by_provider", "by_provider" in costs)
check("get_cost_summary() has recent", "recent" in costs)
check("total_cost is number", isinstance(costs["total_cost"], (int, float)))
check("total_requests is number", isinstance(costs["total_requests"], int))
check("by_provider is list", isinstance(costs["by_provider"], list))
check("recent is list", isinstance(costs["recent"], list))

# ═══════════════════════════════════════════════════════════════════════════════
# 6. _get_session fallback
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. _get_session fallback ---")

# db_utils._get_session() should work (returns a context manager)
session_ctx = db_utils._get_session()
check("_get_session() returns context manager", hasattr(session_ctx, "__enter__"))

with session_ctx as s:
    from sqlalchemy import text
    result = s.execute(text("SELECT 1"))
    check("Session can execute queries", result.scalar() == 1)

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Empty-result edge cases
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 7. Edge cases ---")

# Update non-existent job (should not raise)
db_utils.update_job("nonexistent_job", status="running")
check("update_job() on missing job does not raise", True)

# Update non-existent run (should not raise)
db_utils.update_run("nonexistent_run", status="completed")
check("update_run() on missing run does not raise", True)

# Update non-existent scene (should not raise)
db_utils.update_scene(99999, status="done")
check("update_scene() on missing scene does not raise", True)

# create_run with minimal args
min_run_id = "dbutils_min_run"
db_utils.create_run(run_id=min_run_id)
check("create_run() with minimal args", True)
with get_session() as s:
    r = s.get(Run, min_run_id)
    check("Minimal run created", r is not None)
    check("Minimal run status == running", r.status == "running")
    s.delete(r)

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
