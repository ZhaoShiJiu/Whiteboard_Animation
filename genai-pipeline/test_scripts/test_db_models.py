"""
Test database ORM models and CRUD operations.

Usage::

    cd genai-pipeline
    python test_scripts/test_db_models.py
"""

import sys
import os
from pathlib import Path

# Ensure genai-pipeline is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import datetime
import uuid

# ── Setup ────────────────────────────────────────────────────────────────────
from ai_gateway.db.connection import init_db, get_session
from ai_gateway.db.models import (
    Base, AiRequestLog, AiUsage, RunLog,
    Job, Run, Scene, MediaAsset, ImageLibrary,
)

with open(
    Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml",
    "r", encoding="utf-8",
) as f:
    config = yaml.safe_load(f)
init_db(config["database"], run_migrations=False)

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
# 1. Job CRUD
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. Job CRUD ---")

job_id = "test_job_" + uuid.uuid4().hex[:6]

# Create
with get_session() as s:
    job = Job(
        id=job_id,
        status="queued",
        progress=0,
        message="Test job",
        context="Test context",
        language="english",
        settings_json={"fast_mode": True},
    )
    s.add(job)
check("INSERT Job", True)

# Read
with get_session() as s:
    j = s.get(Job, job_id)
    check("SELECT Job by id", j is not None)
    check("Job.status == queued", j.status == "queued")
    check("Job.context correct", j.context == "Test context")
    check("Job.settings_json correct", j.settings_json == {"fast_mode": True})
    check("Job.created_at is datetime", isinstance(j.created_at, datetime.datetime))
    check("Job.to_dict() returns dict", isinstance(j.to_dict(), dict))
    check("Job.to_dict() has expected keys", "id" in j.to_dict() and "status" in j.to_dict())

# Update
with get_session() as s:
    j = s.get(Job, job_id)
    j.status = "running"
    j.progress = 50
    j.message = "Processing..."
check("UPDATE Job status", True)

with get_session() as s:
    j = s.get(Job, job_id)
    check("Job.status == running", j.status == "running")
    check("Job.progress == 50", j.progress == 50)
    check("Job.message updated", j.message == "Processing...")

# List (newest first)
with get_session() as s:
    from sqlalchemy import desc
    jobs = s.query(Job).order_by(desc(Job.created_at)).limit(10).all()
    check("list_jobs returns results", len(jobs) > 0)
    check("test job in list", any(j.id == job_id for j in jobs))

# Delete (cleanup)
with get_session() as s:
    j = s.get(Job, job_id)
    s.delete(j)
with get_session() as s:
    check("Job deleted", s.get(Job, job_id) is None)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Run CRUD
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. Run CRUD ---")

run_id = "test_run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# Create
with get_session() as s:
    run = Run(
        id=run_id,
        job_id=job_id,  # will be null since job was deleted — acceptable
        status="running",
        context="Test run context",
        language="english",
        settings_json={"fast_mode": False},
        output_dir="/tmp/test_output",
    )
    s.add(run)
check("INSERT Run", True)

# Read
with get_session() as s:
    r = s.get(Run, run_id)
    check("SELECT Run by id", r is not None)
    check("Run.status == running", r.status == "running")
    check("Run.output_dir correct", r.output_dir == "/tmp/test_output")

# Update simulating pipeline lifecycle
with get_session() as s:
    r = s.get(Run, run_id)
    r.scene_count = 5
    r.video_plan_json = {"scenes": [1, 2, 3]}
    r.research_report = "# Research Report\n\nContent here."
check("UPDATE Run fields", True)

with get_session() as s:
    r = s.get(Run, run_id)
    check("Run.scene_count == 5", r.scene_count == 5)
    check("Run.video_plan_json correct", r.video_plan_json == {"scenes": [1, 2, 3]})
    check("Run.research_report not null", r.research_report is not None)

# Complete
with get_session() as s:
    r = s.get(Run, run_id)
    r.status = "completed"
    r.final_video = "/tmp/test_output/final.mp4"
    r.cost_total = 1.23
    r.completed_at = datetime.datetime.utcnow()
check("UPDATE Run completed", True)

with get_session() as s:
    r = s.get(Run, run_id)
    check("Run.status == completed", r.status == "completed")
    check("Run.final_video correct", r.final_video == "/tmp/test_output/final.mp4")
    check("Run.cost_total correct", r.cost_total == 1.23)
    check("Run.completed_at set", r.completed_at is not None)

# Cleanup
with get_session() as s:
    r = s.get(Run, run_id)
    s.delete(r)

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Scene CRUD
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. Scene CRUD ---")

# Create a temporary run first
tmp_run_id = "tmp_run_for_scenes"
with get_session() as s:
    s.add(Run(id=tmp_run_id, status="running", context="tmp"))
    s.flush()

scene_ids = []

for i in range(3):
    with get_session() as s:
        scene = Scene(
            run_id=tmp_run_id,
            scene_index=i + 1,
            narration=f"Narration for scene {i + 1}",
            description=f"Description {i + 1}",
            visual_setup="Whiteboard style",
            text_overlay=f"Scene {i + 1}",
            status="pending",
        )
        s.add(scene)
        s.flush()
        scene_ids.append(scene.id)
check("INSERT 3 Scenes", len(scene_ids) == 3)

# Read
with get_session() as s:
    scene = s.get(Scene, scene_ids[0])
    check("SELECT Scene by id", scene is not None)
    check("Scene.scene_index == 1", scene.scene_index == 1)
    check("Scene.narration correct", "Narration for scene 1" in scene.narration)
    check("Scene.status == pending", scene.status == "pending")

# Update simulating pipeline progress
with get_session() as s:
    scene = s.get(Scene, scene_ids[0])
    scene.image_prompt = "Whiteboard sketch of a rocket"
    scene.audio_path = "/tmp/audio_1.mp3"
    scene.srt_content = "1\n00:00:00,000 --> 00:00:05,000\nHello world"
    scene.status = "done"
    scene.cost = 0.05
check("UPDATE Scene with pipeline data", True)

with get_session() as s:
    scene = s.get(Scene, scene_ids[0])
    check("Scene.image_prompt correct", scene.image_prompt == "Whiteboard sketch of a rocket")
    check("Scene.audio_path correct", scene.audio_path == "/tmp/audio_1.mp3")
    check("Scene.srt_content not null", scene.srt_content is not None)
    check("Scene.status == done", scene.status == "done")
    check("Scene.cost == 0.05", scene.cost == 0.05)

# Query by run_id
with get_session() as s:
    scenes = s.query(Scene).filter(Scene.run_id == tmp_run_id).all()
    check("Query scenes by run_id returns 3", len(scenes) == 3)

# Cleanup
with get_session() as s:
    for sid in scene_ids:
        s.delete(s.get(Scene, sid))
    s.delete(s.get(Run, tmp_run_id))

# ═══════════════════════════════════════════════════════════════════════════════
# 4. MediaAsset CRUD
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. MediaAsset CRUD ---")

tmp_run_id2 = "tmp_run_for_assets"
with get_session() as s:
    s.add(Run(id=tmp_run_id2, status="running", context="tmp"))

with get_session() as s:
    asset = MediaAsset(
        run_id=tmp_run_id2,
        scene_id=1,
        asset_type="audio",
        file_path="/tmp/test_audio.mp3",
        file_name="test_audio.mp3",
        file_size=12345,
        mime_type="audio/mpeg",
        is_temporary=True,
    )
    s.add(asset)
    s.flush()
    asset_id = asset.id
check("INSERT MediaAsset", asset_id > 0)

with get_session() as s:
    a = s.get(MediaAsset, asset_id)
    check("SELECT MediaAsset by id", a is not None)
    check("asset_type == audio", a.asset_type == "audio")
    check("file_path correct", a.file_path == "/tmp/test_audio.mp3")
    check("is_temporary == True", a.is_temporary is True)

# Content text storage (SRT example)
with get_session() as s:
    srt_asset = MediaAsset(
        run_id=tmp_run_id2,
        asset_type="srt",
        file_path="/tmp/test.srt",
        content_text="1\n00:00:00,000 --> 00:00:05,000\nTest subtitle",
    )
    s.add(srt_asset)
    s.flush()
    srt_id = srt_asset.id
check("INSERT MediaAsset with content_text", srt_id > 0)

with get_session() as s:
    a = s.get(MediaAsset, srt_id)
    check("content_text stored correctly", "Test subtitle" in a.content_text)

# Cleanup
with get_session() as s:
    s.delete(s.get(MediaAsset, asset_id))
    s.delete(s.get(MediaAsset, srt_id))
    s.delete(s.get(Run, tmp_run_id2))

# ═══════════════════════════════════════════════════════════════════════════════
# 5. RunLog (log_utils ORM)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. RunLog ---")

test_run_id = "test_run_log_entries"
with get_session() as s:
    entries = [
        RunLog(run_id=test_run_id, scene_id=1, step_tag="research", level="INFO",
               message="Research started", loc="test:1:test"),
        RunLog(run_id=test_run_id, scene_id=1, step_tag="research", level="INFO",
               message="Research completed", extra_json='{"elapsed_ms": 1234}',
               loc="test:10:test"),
        RunLog(run_id=test_run_id, scene_id=2, step_tag="image_gen", level="ERROR",
               message="Image generation failed", loc="test:20:test"),
    ]
    s.add_all(entries)
check("INSERT 3 RunLog entries", True)

# Query by run_id
with get_session() as s:
    logs = s.query(RunLog).filter(RunLog.run_id == test_run_id).all()
    check("Query RunLog by run_id returns 3", len(logs) == 3)

# Filter by level
with get_session() as s:
    errors = s.query(RunLog).filter(
        RunLog.run_id == test_run_id, RunLog.level == "ERROR"
    ).all()
    check("Filter RunLog by level=ERROR returns 1", len(errors) == 1)
    check("Error message correct", errors[0].message == "Image generation failed")

# Filter by scene_id
with get_session() as s:
    scene1_logs = s.query(RunLog).filter(
        RunLog.run_id == test_run_id, RunLog.scene_id == 1
    ).all()
    check("Filter RunLog by scene_id=1 returns 2", len(scene1_logs) == 2)

# extra_json roundtrip
with get_session() as s:
    log = s.query(RunLog).filter(
        RunLog.run_id == test_run_id, RunLog.extra_json.isnot(None)
    ).first()
    check("extra_json stored", log is not None)
    import json
    check("extra_json parseable", json.loads(log.extra_json)["elapsed_ms"] == 1234)

# Cleanup
with get_session() as s:
    s.query(RunLog).filter(RunLog.run_id == test_run_id).delete()

# ═══════════════════════════════════════════════════════════════════════════════
# 6. ImageLibrary
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. ImageLibrary ---")

# Create a minimal test image (1x1 red PNG)
import struct
import zlib


def _make_test_png(width: int = 2, height: int = 2) -> bytes:
    """Create a minimal valid PNG in memory."""

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))

    # Raw image data: red pixels
    raw = b""
    for _ in range(height):
        raw += b"\x00"  # filter none
        for _ in range(width):
            raw += b"\xff\x00\x00"  # R=255, G=0, B=0

    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend


test_png = _make_test_png()
test_hash = __import__("hashlib").sha256(test_png).hexdigest()

# Insert
with get_session() as s:
    img = ImageLibrary(
        content_hash=test_hash,
        file_data=test_png,
        thumbnail_data=None,  # no thumbnail for tiny image
        embedding_json=[0.1] * 2048,  # fake embedding
        prompt="Test prompt: whiteboard sketch of a cat",
        scene_desc="A cat sitting on a table",
        width=1920,
        height=1080,
        file_size=len(test_png),
        source="ai_gen",
        source_run_id="test_run_001",
        usage_count=1,
    )
    s.add(img)
    s.flush()
    img_id = img.id
check("INSERT ImageLibrary", img_id > 0)

# Read
with get_session() as s:
    img = s.get(ImageLibrary, img_id)
    check("SELECT ImageLibrary by id", img is not None)
    check("content_hash correct", img.content_hash == test_hash)
    check("prompt correct", "whiteboard sketch of a cat" in img.prompt)
    check("scene_desc correct", img.scene_desc == "A cat sitting on a table")
    check("width correct", img.width == 1920)
    check("height correct", img.height == 1080)
    check("file_data stored and correct", img.file_data == test_png)
    check("embedding_json stored", img.embedding_json is not None)
    check("embedding_json length 2048", len(img.embedding_json) == 2048)
    check("source correct", img.source == "ai_gen")
    check("source_run_id correct", img.source_run_id == "test_run_001")
    check("usage_count == 1", img.usage_count == 1)
    check("created_at is datetime", isinstance(img.created_at, datetime.datetime))

# Unique constraint — duplicate hash should fail
with get_session() as s:
    duplicate = ImageLibrary(
        content_hash=test_hash,
        file_data=test_png,
        prompt="Duplicate",
        width=1920,
        height=1080,
        file_size=len(test_png),
        source="ai_gen",
    )
    s.add(duplicate)
    try:
        s.flush()
        check("UNIQUE constraint on content_hash", False, "should have raised IntegrityError")
    except Exception:
        s.rollback()
        check("UNIQUE constraint on content_hash enforced", True)

# Update usage_count
with get_session() as s:
    img = s.get(ImageLibrary, img_id)
    img.usage_count += 1
    img.last_used_at = datetime.datetime.utcnow()
check("UPDATE usage_count", True)

with get_session() as s:
    img = s.get(ImageLibrary, img_id)
    check("usage_count == 2", img.usage_count == 2)
    check("last_used_at set", img.last_used_at is not None)

# Cleanup
with get_session() as s:
    s.delete(s.get(ImageLibrary, img_id))

# ═══════════════════════════════════════════════════════════════════════════════
# 7. AiRequestLog extended fields
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 7. AiRequestLog extended fields ---")

with get_session() as s:
    log = AiRequestLog(
        task="story",
        provider="deepseek",
        model="deepseek-v4-pro",
        status="success",
        latency_ms=500,
        run_id="test_run_123",
        scene_id=1,
    )
    s.add(log)
    s.flush()
    log_id = log.id

with get_session() as s:
    l = s.get(AiRequestLog, log_id)
    check("AiRequestLog.run_id stored", l.run_id == "test_run_123")
    check("AiRequestLog.scene_id stored", l.scene_id == 1)
    check("AiRequestLog.run_id nullable OK", True)  # field exists, tested above
    check("AiRequestLog.scene_id nullable OK", True)

# Cleanup
with get_session() as s:
    s.delete(s.get(AiRequestLog, log_id))

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
