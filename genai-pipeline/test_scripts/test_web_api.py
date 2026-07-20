"""
Test the web_app API endpoints using Flask's test client.

Does NOT start a server — uses Werkzeug's test client internally.
Requires the DB to be reachable.

Usage::

    cd genai-pipeline
    python test_scripts/test_web_api.py
"""

import sys
import os
from pathlib import Path

# Add both genai-pipeline and project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import json
import yaml
import uuid

from ai_gateway.db.connection import init_db
from ai_gateway.db.models import Job, Run, RunLog, MediaAsset, ImageLibrary

# Init DB before importing web_app (which may trigger Gateway init)
with open(
    Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml",
    "r", encoding="utf-8",
) as f:
    config = yaml.safe_load(f)
init_db(config["database"], run_migrations=False)

from web_app.app import app

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


# Create test client
client = app.test_client()

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Health check
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 1. Health check ---")

resp = client.get("/api/health")
check("GET /api/health returns 200", resp.status_code == 200)
data = json.loads(resp.data)
check("Health status is ok", data["status"] == "ok")
check("Health has timestamp", "timestamp" in data)

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Job creation and retrieval
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 2. Job API ---")

# Create job
resp = client.post(
    "/api/jobs",
    data=json.dumps({
        "context": "Test video topic",
        "language": "english",
        "research_mode": "web",
        "fast_mode": False,
        "reference_images": True,
    }),
    content_type="application/json",
)
check("POST /api/jobs returns 201", resp.status_code == 201)
job_data = json.loads(resp.data)
check("Job has id", "id" in job_data)
check("Job has status queued", job_data["status"] == "queued")
check("Job has progress 0", job_data["progress"] == 0)
check("Job context matches", job_data["context"] == "Test video topic")
check("Job language matches", job_data["language"] == "english")
check("Job has created_at", "created_at" in job_data)

job_id = job_data["id"]

# Get single job
resp = client.get(f"/api/jobs/{job_id}")
check("GET /api/jobs/<id> returns 200", resp.status_code == 200)
check("GET /api/jobs/<id> matches created job", json.loads(resp.data)["id"] == job_id)

# List jobs
resp = client.get("/api/jobs")
check("GET /api/jobs returns 200", resp.status_code == 200)
jobs_list = json.loads(resp.data)
check("GET /api/jobs returns list", isinstance(jobs_list, list))
check("Created job appears in list", any(j["id"] == job_id for j in jobs_list))

# Missing job
resp = client.get("/api/jobs/nonexistent123")
check("GET /api/jobs/<missing> returns 404", resp.status_code == 404)

# Missing context
resp = client.post(
    "/api/jobs",
    data=json.dumps({"context": ""}),
    content_type="application/json",
)
check("POST /api/jobs with empty context returns 400", resp.status_code == 400)

# Missing body
resp = client.post("/api/jobs", data="", content_type="application/json")
check("POST /api/jobs with no body returns 400", resp.status_code in (400, 415))

# Cleanup job
from tools import db_utils
job_id_cleanup = job_id

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Outputs API
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 3. Outputs API ---")

resp = client.get("/api/outputs")
check("GET /api/outputs returns 200", resp.status_code == 200)
outputs = json.loads(resp.data)
check("GET /api/outputs returns list", isinstance(outputs, list))
# May be empty if no runs exist — that's OK
check("GET /api/outputs is valid JSON", True)

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Costs API
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 4. Costs API ---")

resp = client.get("/api/costs")
check("GET /api/costs returns 200", resp.status_code == 200)
cost_data = json.loads(resp.data)
check("Cost data has total_cost", "total_cost" in cost_data)
check("Cost data has total_requests", "total_requests" in cost_data)
check("Cost data has by_provider", "by_provider" in cost_data)
check("Cost data has recent", "recent" in cost_data)
check("total_cost is number", isinstance(cost_data["total_cost"], (int, float)))
check("total_requests is number", isinstance(cost_data["total_requests"], int))

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Logs API
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 5. Logs API ---")

# Insert some test logs
test_run_id = "api_test_run_001"
from ai_gateway.db.connection import get_session

with get_session() as s:
    s.add_all([
        RunLog(run_id=test_run_id, scene_id=1, step_tag="research",
               level="INFO", message="Research started"),
        RunLog(run_id=test_run_id, scene_id=1, step_tag="research",
               level="INFO", message="Research completed",
               extra_json='{"elapsed_ms": 500}'),
        RunLog(run_id=test_run_id, scene_id=2, step_tag="image_gen",
               level="ERROR", message="Generation failed"),
        RunLog(run_id=test_run_id, scene_id=2, step_tag="image_gen",
               level="WARNING", message="Retrying..."),
    ])

# Fetch logs
resp = client.get(f"/api/logs/{test_run_id}")
check("GET /api/logs/<run_id> returns 200", resp.status_code == 200)
log_data = json.loads(resp.data)
check("Log data has run_id", log_data["run_id"] == test_run_id)
check("Log data has entries", "entries" in log_data)
check("Log data has 4 entries", log_data["count"] == 4)
check("Entries are list", isinstance(log_data["entries"], list))

# Filter by level
resp = client.get(f"/api/logs/{test_run_id}?level=ERROR")
check("Log filter by level returns 200", resp.status_code == 200)
err_data = json.loads(resp.data)
check("Log filter by ERROR returns 1 entry", err_data["count"] == 1)

# Filter by scene
resp = client.get(f"/api/logs/{test_run_id}?scene_id=1")
check("Log filter by scene returns 200", resp.status_code == 200)
scene_data = json.loads(resp.data)
check("Log filter by scene=1 returns 2 entries", scene_data["count"] == 2)

# Filter by limit
resp = client.get(f"/api/logs/{test_run_id}?limit=2")
check("Log limit returns 200", resp.status_code == 200)
limit_data = json.loads(resp.data)
check("Log limit=2 returns 2 entries", limit_data["count"] == 2)

# Log stats
resp = client.get("/api/logs/stats")
check("GET /api/logs/stats returns 200", resp.status_code == 200)
stats = json.loads(resp.data)
check("Stats has runs count", "runs" in stats)
check("Stats has total_entries", "total_entries" in stats)
check("Stats has by_level", "by_level" in stats)
check("Stats has recent_runs", "recent_runs" in stats)

# Cleanup
with get_session() as s:
    s.query(RunLog).filter(RunLog.run_id == test_run_id).delete()

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Image library API
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 6. Image library API ---")

import hashlib
import struct
import zlib


def _make_test_png(w=2, h=2) -> bytes:
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    hdr = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    raw = b""
    for _ in range(h):
        raw += b"\x00" + b"\xff\x00\x00" * w
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return hdr + ihdr + idat + iend


test_png = _make_test_png()
test_hash = hashlib.sha256(test_png).hexdigest()

with get_session() as s:
    img = ImageLibrary(
        content_hash=test_hash,
        file_data=test_png,
        thumbnail_data=_make_test_png(8, 4),
        prompt="API test prompt",
        scene_desc="API test scene",
        width=1920,
        height=1080,
        file_size=len(test_png),
        source="ai_gen",
        usage_count=3,
    )
    s.add(img)
    s.flush()
    api_img_id = img.id

# List images
resp = client.get("/api/images")
check("GET /api/images returns 200", resp.status_code == 200)
img_data = json.loads(resp.data)
check("Image list has total", "total" in img_data)
check("Image list has page", "page" in img_data)
check("Image list has images array", "images" in img_data)
check("Image list total > 0", img_data["total"] > 0)

our_img = next((i for i in img_data["images"] if i["id"] == api_img_id), None)
check("Our image is in list", our_img is not None)
if our_img:
    check("Image has content_hash", len(our_img["content_hash"]) == 64)
    check("Image has prompt", our_img["prompt"] == "API test prompt")
    check("Image has width", our_img["width"] == 1920)
    check("Image has height", our_img["height"] == 1080)
    check("Image has file_size", our_img["file_size"] == len(test_png))
    check("Image has source", our_img["source"] == "ai_gen")
    check("Image has usage_count", our_img["usage_count"] == 3)
    check("Image has has_thumbnail", our_img["has_thumbnail"] is True)
    check("Image has created_at", our_img["created_at"] is not None)

# Thumbnail
resp = client.get(f"/api/images/{api_img_id}/thumbnail")
check("GET /api/images/<id>/thumbnail returns 200", resp.status_code == 200)
check("Thumbnail content-type is image/jpeg", resp.content_type == "image/jpeg")

# Missing thumbnail
resp = client.get("/api/images/99999/thumbnail")
check("GET /api/images/99999/thumbnail returns 404", resp.status_code == 404)

# Cleanup
with get_session() as s:
    s.delete(s.get(ImageLibrary, api_img_id))

# ═══════════════════════════════════════════════════════════════════════════════
# 7. Index page
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 7. Index page ---")

resp = client.get("/")
check("GET / returns 200", resp.status_code == 200)
check("Response is HTML", b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data)

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Video output serving (expects missing file)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- 8. Video output serving ---")

resp = client.get("/api/outputs/nonexistent_run/test.mp4")
check("GET /api/outputs/<missing> returns 404", resp.status_code == 404)

# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════════════════════
with get_session() as s:
    j = s.get(Job, job_id_cleanup)
    if j:
        s.delete(j)

# ═══════════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 50}")
print(f"Results: {_passed} passed, {_failed} failed")
if _failed:
    print("Some tests FAILED — check output above.")
else:
    print("All tests passed!")
