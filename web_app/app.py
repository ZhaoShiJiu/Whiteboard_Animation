"""
Storyboard AI — Web Frontend
Flask backend that wraps the GenAI pipeline and serves the web UI.
"""

import logging
import logging.handlers
import os
import sys
import json
import threading
import datetime
import uuid
import sqlite3
import traceback
import time
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, request, jsonify, send_from_directory, g

# ── Path setup: add parent so we can import genai-pipeline modules ──────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "genai-pipeline"))

# ── Application logging setup ────────────────────────────────────────────────
_logs_dir = BASE_DIR / "logs"
_logs_dir.mkdir(exist_ok=True)

# Console handler (human-readable)
# Use stdout — Docker json-file driver captures both streams, but some log viewers
# only show stdout by default, so stderr logs appear "missing".
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
))

# File handler (JSON lines for web server logs)
_file_handler = logging.handlers.RotatingFileHandler(
    str(_logs_dir / "web_app.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)

class _WebAppJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "loc": f"{record.pathname}:{record.lineno}:{record.funcName}",
        }
        if record.exc_info and record.exc_info[0]:
            obj["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        return json.dumps(obj, ensure_ascii=False, default=str)

_file_handler.setFormatter(_WebAppJsonFormatter())

# Root logger for web app
_web_logger = logging.getLogger("web_app")
_web_logger.setLevel(logging.DEBUG)
_web_logger.addHandler(_console_handler)
_web_logger.addHandler(_file_handler)
_web_logger.propagate = False

# Also capture Flask's own logger output
_flask_logger = logging.getLogger("flask.app")
_flask_logger.handlers.clear()
_flask_logger.addHandler(_console_handler)
_flask_logger.addHandler(_file_handler)

# Also capture Werkzeug's request-access logger (Flask dev server uses it for
# "GET /api/health HTTP/1.1" 200 - style messages)
_werkzeug_logger = logging.getLogger("werkzeug")
_werkzeug_logger.handlers.clear()
_werkzeug_logger.addHandler(_console_handler)
_werkzeug_logger.addHandler(_file_handler)

app = Flask(__name__, static_folder="static", template_folder="templates")

# ── In-memory job store ─────────────────────────────────────────────────────
jobs: dict = {}          # job_id → {status, progress, result, ...}
jobs_lock = threading.Lock()

# ── Request logging middleware ───────────────────────────────────────────────

@app.before_request
def _before_request():
    g._request_start = time.perf_counter()

@app.after_request
def _after_request(response):
    elapsed_ms = int((time.perf_counter() - g.get("_request_start", time.perf_counter())) * 1000)
    _web_logger.info(
        "%s %s → %s %dms",
        request.method, request.path, response.status, elapsed_ms,
    )
    return response

# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main SPA page."""
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.datetime.now().isoformat()})


@app.route("/api/jobs", methods=["POST"])
def create_job():
    """Create a new pipeline job."""
    data = request.get_json() or {}

    context = data.get("context", "").strip()
    if not context:
        return jsonify({"error": "Please provide a topic / context."}), 400

    language = data.get("language", "english").strip() or "english"
    do_research = data.get("research_mode") == "deep"
    do_web_search = data.get("research_mode") == "web"
    use_internet_image_search = data.get("reference_images", True)
    fast_mode = data.get("fast_mode", False)

    # Video provider: "seedance", "happyhorse", or None (skip video generation)
    video_provider = data.get("video_provider") or None
    # Backward compatibility: old clients sending enable_veo=true without video_provider
    if video_provider is None and data.get("enable_veo"):
        video_provider = "seedance"
    veo_direction_by_director = bool(video_provider) and data.get("veo_direction", True)

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Job queued…",
        "context": context,
        "language": language,
        "video_provider": video_provider,
        "created_at": datetime.datetime.now().isoformat(),
        "result": None,
        "error": None,
    }
    with jobs_lock:
        jobs[job_id] = job

    _web_logger.info(
        "Job created: %s (context=%s, language=%s, fast_mode=%s, video_provider=%s)",
        job_id, context[:100], language, fast_mode, video_provider,
    )

    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, context, do_research, do_web_search,
              use_internet_image_search, fast_mode, language,
              video_provider, veo_direction_by_director),
        daemon=True,
    )
    thread.start()

    return jsonify(job), 201


@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    """Return all jobs, newest first."""
    with jobs_lock:
        return jsonify(sorted(jobs.values(), key=lambda j: j["created_at"], reverse=True))


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@app.route("/api/outputs")
def list_outputs():
    """List generated output videos."""
    output_root = PROJECT_DIR / "genai-pipeline" / "output"
    runs = []
    if output_root.exists():
        for run_dir in sorted(output_root.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            # Check for run.log
            log_file = run_dir / "run.log"
            video_path = run_dir / "storyboard_final_video.mp4"
            single_scenes = sorted(run_dir.glob("scene_*_final.mp4"))
            runs.append({
                "run_id": run_dir.name,
                "created_at": _parse_timestamp(run_dir.name),
                "has_final_video": video_path.exists(),
                "video_name": video_path.name if video_path.exists() else None,
                "scene_count": len(single_scenes),
                "has_log": log_file.exists(),
            })
    return jsonify(runs)


@app.route("/api/outputs/<run_id>/<filename>")
def serve_output(run_id, filename):
    """Serve a generated video file."""
    output_dir = PROJECT_DIR / "genai-pipeline" / "output" / run_id
    return send_from_directory(str(output_dir), filename)


@app.route("/api/costs")
def cost_summary():
    """Return cost data from the AI Gateway SQLite database."""
    db_path = PROJECT_DIR / "genai-pipeline" / "ai_gateway.db"
    if not db_path.exists():
        return jsonify({"total_cost": 0, "total_requests": 0, "by_provider": [], "recent": []})

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(cost), 0) as total FROM request_logs")
        summary = dict(cursor.fetchone())

        cursor.execute(
            "SELECT provider, COUNT(*) as cnt, COALESCE(SUM(cost), 0) as total_cost "
            "FROM request_logs GROUP BY provider ORDER BY total_cost DESC"
        )
        by_provider = [dict(r) for r in cursor.fetchall()]

        cursor.execute(
            "SELECT * FROM request_logs ORDER BY created_at DESC LIMIT 20"
        )
        recent = [dict(r) for r in cursor.fetchall()]

        conn.close()
        return jsonify({
            "total_cost": round(summary["total"] or 0, 6),
            "total_requests": summary["cnt"] or 0,
            "by_provider": by_provider,
            "recent": recent,
        })
    except Exception as e:
        _web_logger.error("Cost summary query failed: %s", e)
        return jsonify({"error": str(e), "total_cost": 0, "total_requests": 0, "by_provider": [], "recent": []})


# ── Log API endpoints ───────────────────────────────────────────────────────

@app.route("/api/logs/<run_id>")
def get_run_logs(run_id):
    """
    Fetch logs for a specific pipeline run.

    Query params:
        level: filter by level (DEBUG/INFO/WARNING/ERROR)
        scene_id: filter by scene number
        limit: max entries (default 500)
        source: "file" (default, reads run.log) or "db" (queries run_logs table)
    """
    level_filter = request.args.get("level")
    scene_filter = request.args.get("scene_id")
    try:
        limit = int(request.args.get("limit", 500))
    except (ValueError, TypeError):
        limit = 500
    source = request.args.get("source", "file")

    if source == "db":
        return _get_logs_from_db(run_id, level_filter, scene_filter, limit)
    else:
        return _get_logs_from_file(run_id, level_filter, scene_filter, limit)


def _get_logs_from_file(run_id: str, level: Optional[str], scene_id: Optional[str], limit: int):
    """Read logs from output/<run_id>/run.log."""
    log_file = PROJECT_DIR / "genai-pipeline" / "output" / run_id / "run.log"
    if not log_file.exists():
        return jsonify({"error": f"Log file not found for run: {run_id}", "entries": []}), 404

    entries = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if level and entry.get("level") != level.upper():
                        continue
                    if scene_id is not None:
                        try:
                            if entry.get("scene_id") != int(scene_id):
                                continue
                        except (ValueError, TypeError):
                            continue
                    entries.append(entry)
                    if len(entries) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        _web_logger.error("Error reading log file: %s", e)
        return jsonify({"error": str(e), "entries": []}), 500

    return jsonify({"run_id": run_id, "count": len(entries), "entries": entries})


def _get_logs_from_db(run_id: str, level: Optional[str], scene_id: Optional[str], limit: int):
    """Read logs from the run_logs table in ai_gateway.db."""
    db_path = PROJECT_DIR / "genai-pipeline" / "ai_gateway.db"
    if not db_path.exists():
        return jsonify({"error": "Database not found", "entries": []}), 404

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = "SELECT * FROM run_logs WHERE run_id = ?"
        params = [run_id]

        if level:
            query += " AND level = ?"
            params.append(level.upper())
        if scene_id is not None:
            try:
                query += " AND scene_id = ?"
                params.append(int(scene_id))
            except (ValueError, TypeError):
                pass

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return jsonify({"run_id": run_id, "count": len(rows), "entries": rows})
    except Exception as e:
        _web_logger.error("Error querying run_logs: %s", e)
        return jsonify({"error": str(e), "entries": []}), 500


@app.route("/api/logs/stats")
def log_stats():
    """Return aggregate log statistics."""
    db_path = PROJECT_DIR / "genai-pipeline" / "ai_gateway.db"
    if not db_path.exists():
        return jsonify({"runs": 0, "total_entries": 0, "by_level": {}, "recent_runs": []})

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if run_logs table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='run_logs'"
        )
        if not cursor.fetchone():
            conn.close()
            return jsonify({"runs": 0, "total_entries": 0, "by_level": {}, "recent_runs": []})

        cursor.execute("SELECT COUNT(DISTINCT run_id) as cnt FROM run_logs")
        runs = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) as cnt FROM run_logs")
        total_entries = cursor.fetchone()["cnt"]

        cursor.execute(
            "SELECT level, COUNT(*) as cnt FROM run_logs GROUP BY level ORDER BY cnt DESC"
        )
        by_level = {r["level"]: r["cnt"] for r in cursor.fetchall()}

        cursor.execute(
            "SELECT run_id, COUNT(*) as cnt, MIN(created_at) as first_ts, MAX(created_at) as last_ts "
            "FROM run_logs GROUP BY run_id ORDER BY last_ts DESC LIMIT 10"
        )
        recent_runs = [dict(r) for r in cursor.fetchall()]

        conn.close()
        return jsonify({
            "runs": runs,
            "total_entries": total_entries,
            "by_level": by_level,
            "recent_runs": recent_runs,
        })
    except Exception as e:
        _web_logger.error("Error querying log stats: %s", e)
        return jsonify({"error": str(e), "runs": 0, "total_entries": 0, "by_level": {}, "recent_runs": []})


# ── Background job runner ───────────────────────────────────────────────────

def _run_pipeline_job(job_id, context, do_research, do_web_search,
                      use_internet_image_search, fast_mode, language,
                      video_provider, veo_direction_by_director):
    """Execute the pipeline in a background thread, updating job progress."""
    try:
        _update_job(job_id, "running", 5, "Starting pipeline…")
        _web_logger.info("Job %s: Starting pipeline execution", job_id)

        # Deferred import so the web app can start even if some deps are missing
        from pipeline import run_pipeline

        _update_job(job_id, "running", 10, "Research & planning…")

        result = run_pipeline(
            user_context=context,
            do_research=do_research,
            do_web_search=do_web_search,
            use_internet_image_search=use_internet_image_search,
            fast_mode=fast_mode,
            language=language,
            video_provider=video_provider,
            veo_direction_by_director=veo_direction_by_director,
        )

        if result and os.path.exists(result):
            _update_job(job_id, "completed", 100, "Video ready!", result=result)
            _web_logger.info("Job %s: Completed successfully — %s", job_id, result)
        else:
            _update_job(job_id, "failed", 100, "Pipeline finished but no video was produced.",
                        error="No output video generated.")
            _web_logger.warning("Job %s: Finished but no video produced", job_id)

    except Exception as exc:
        _update_job(job_id, "failed", 0, f"Error: {exc}", error=str(exc))
        _web_logger.error("Job %s: Failed — %s\n%s", job_id, exc, traceback.format_exc())


def _update_job(job_id, status, progress, message, result=None, error=None):
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            job["status"] = status
            job["progress"] = progress
            job["message"] = message
            if result:
                job["result"] = result
            if error:
                job["error"] = error


def _parse_timestamp(dir_name: str) -> str:
    """Try to extract a readable datetime from a run_YYYYMMDD_HHMMSS name."""
    try:
        part = dir_name.replace("run_", "")
        dt = datetime.datetime.strptime(part, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return dir_name


# ── Module-level startup log ─────────────────────────────────────────────────
# Placed at module level (NOT inside ``if __name__ == "__main__":``) so it fires
# regardless of whether the app is started via ``python app.py`` or
# ``python -m flask --app web_app/app.py run`` (the latter imports the module;
# __name__ != "__main__" and the block below never executes).

_startup_host = os.environ.get("WEB_HOST", "127.0.0.1")
_startup_port = int(os.environ.get("WEB_PORT", 5000))
_startup_debug = os.environ.get("WEB_DEBUG", "1") == "1"

_web_logger.info("=" * 60)
_web_logger.info("  Storyboard AI — Web UI")
_web_logger.info("  Listening on http://%s:%s", _startup_host, _startup_port)
_web_logger.info("  Debug mode: %s", _startup_debug)
_web_logger.info("  Logs directory: %s", _logs_dir)
_web_logger.info("=" * 60)


# ── Entry point (direct execution only) ─────────────────────────────────────

if __name__ == "__main__":
    app.run(host=_startup_host, port=_startup_port, debug=_startup_debug)
