"""
Whiteboard Animation AI — Web Frontend
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
import traceback
import time
from pathlib import Path
from typing import Optional

from flask import Flask, render_template, request, jsonify, send_from_directory, g

# ── Path setup: add parent so we can import genai-pipeline modules ──────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "genai-pipeline"))

# In-memory registry of active pipeline runtimes (keyed by job_id)
_active_runtimes: dict = {}

# ── Application logging setup ────────────────────────────────────────────────
_logs_dir = BASE_DIR / "logs"
_logs_dir.mkdir(exist_ok=True)

# Console handler (human-readable)
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

# Also capture Werkzeug's request-access logger
_werkzeug_logger = logging.getLogger("werkzeug")
_werkzeug_logger.handlers.clear()
_werkzeug_logger.addHandler(_console_handler)
_werkzeug_logger.addHandler(_file_handler)

app = Flask(__name__, static_folder="static", template_folder="templates")

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

    # Image provider: "qwen" (default), "doubao_image"
    image_provider = data.get("image_provider", "qwen") or "qwen"

    # Video provider: "seedance", "happyhorse", or None (skip video generation)
    video_provider = data.get("video_provider") or None
    # Backward compatibility: old clients sending enable_veo=true without video_provider
    if video_provider is None and data.get("enable_veo"):
        video_provider = "seedance"
    veo_direction_by_director = bool(video_provider) and data.get("veo_direction", True)

    job_id = uuid.uuid4().hex[:12]

    settings = {
        "research_mode": data.get("research_mode", "web"),
        "fast_mode": fast_mode,
        "image_provider": image_provider,
        "video_provider": video_provider,
        "veo_direction_by_director": veo_direction_by_director,
        "use_internet_image_search": use_internet_image_search,
    }

    # Write job to database
    from tools.db_utils import create_job

    create_job(
        job_id=job_id,
        context=context,
        language=language,
        settings=settings,
    )

    # Update in-memory-like response (backward compatible)
    job_dict = {
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

    _web_logger.info(
        "Job created: %s (context=%s, language=%s, fast_mode=%s, video_provider=%s)",
        job_id, context[:100], language, fast_mode, video_provider,
    )

    thread = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, context, do_research, do_web_search,
              use_internet_image_search, fast_mode, language,
              image_provider, video_provider, veo_direction_by_director),
        daemon=True,
    )
    thread.start()

    return jsonify(job_dict), 201


@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    """Return all jobs, newest first (from database)."""
    from tools.db_utils import list_jobs
    return jsonify(list_jobs())


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    """Get a single job by ID."""
    from tools.db_utils import get_job
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@app.route("/api/jobs/<job_id>/approve", methods=["POST"])
def approve_job(job_id):
    """
    Approve (or request regenerate of) the current pipeline stage.

    The pipeline thread is blocked on a PipelineRuntime.pause_event.
    This endpoint signals that event so the pipeline resumes.

    Request body (all fields optional):
        action: "approve" (default) | "regenerate"
        feedback: user feedback text (used when regenerating)
        video_plan: edited video plan JSON (used when director review is approved)
    """
    runtime = _active_runtimes.get(job_id)
    if not runtime:
        return jsonify({"error": "Job not found or not awaiting review."}), 404

    data = request.get_json() or {}
    action = data.get("action", "approve")
    feedback = data.get("feedback", "").strip()

    if action == "regenerate":
        runtime.regenerate = True
        runtime.feedback = feedback
        _web_logger.info("Job %s: Regenerate requested (feedback=%s)", job_id, feedback[:100])
    else:
        runtime.regenerate = False
        runtime.feedback = ""

    # Accept an edited video_plan from the frontend
    video_plan = data.get("video_plan")
    if video_plan:
        runtime.edited_video_plan = video_plan
        _web_logger.info("Job %s: Edited video_plan received (%d scenes)",
                         job_id, len(video_plan.get("scenes", [])))
        # Persist immediately so the DB is up-to-date
        from tools.db_utils import update_run
        update_run(runtime.run_id, video_plan_json=video_plan,
                   scene_count=len(video_plan.get("scenes", [])))

    runtime.pause_event.set()
    _web_logger.info("Job %s: Review approved, pipeline resuming (action=%s)", job_id, action)
    return jsonify({"status": "ok", "action": action})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    """Cancel a running or reviewing job."""
    runtime = _active_runtimes.get(job_id)
    if runtime:
        runtime.abort_event.set()
        _web_logger.info("Job %s: Cancelled by user", job_id)
        return jsonify({"status": "ok", "message": "Job cancelled."})
    else:
        # Job might not have a runtime yet — update DB directly
        from tools.db_utils import update_job as _uj
        _uj(job_id, status="cancelled", progress=0, message="已取消")
        return jsonify({"status": "ok", "message": "Job marked as cancelled."})


@app.route("/api/outputs")
def list_outputs():
    """List generated output videos from the runs database table."""
    from tools.db_utils import list_runs
    runs = list_runs()
    if runs:
        return jsonify(runs)

    # Fallback: scan filesystem for runs not yet in DB
    output_root = PROJECT_DIR / "genai-pipeline" / "output"
    runs = []
    if output_root.exists():
        for run_dir in sorted(output_root.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            video_path = run_dir / "whiteboard-animation-ai_final_video.mp4"
            single_scenes = sorted(run_dir.glob("scene_*_final.mp4"))
            runs.append({
                "run_id": run_dir.name,
                "created_at": _parse_timestamp(run_dir.name),
                "has_final_video": video_path.exists(),
                "video_name": video_path.name if video_path.exists() else None,
                "scene_count": len(single_scenes),
                "has_log": (run_dir / "run.log").exists(),
                "status": "unknown",
                "context": "",
                "language": "",
                "cost_total": None,
                "duration_sec": None,
            })
    return jsonify(runs)


@app.route("/api/outputs/<run_id>/<filename>")
def serve_output(run_id, filename):
    """Serve a generated video file."""
    output_dir = PROJECT_DIR / "genai-pipeline" / "output" / run_id
    return send_from_directory(str(output_dir), filename)


@app.route("/api/costs")
def cost_summary():
    """Return cost data from the AI Gateway database via ORM."""
    from tools.db_utils import get_cost_summary
    return jsonify(get_cost_summary())


# ── Log API endpoints ───────────────────────────────────────────────────────

@app.route("/api/logs/<run_id>")
def get_run_logs(run_id):
    """
    Fetch logs for a specific pipeline run.

    Query params:
        level: filter by level (DEBUG/INFO/WARNING/ERROR)
        scene_id: filter by scene number
        limit: max entries (default 500)
        source: "file" (reads run.log) or "db" (queries run_logs table, default)
    """
    level_filter = request.args.get("level")
    scene_filter = request.args.get("scene_id")
    try:
        limit = int(request.args.get("limit", 500))
    except (ValueError, TypeError):
        limit = 500
    source = request.args.get("source", "db")

    if source == "file":
        return _get_logs_from_file(run_id, level_filter, scene_filter, limit)
    else:
        return _get_logs_from_db_orm(run_id, level_filter, scene_filter, limit)


def _get_logs_from_file(run_id: str, level: Optional[str], scene_id: Optional[str], limit: int):
    """Read logs from output/<run_id>/run.log (file-based fallback)."""
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


def _get_logs_from_db_orm(run_id: str, level: Optional[str], scene_id: Optional[str], limit: int):
    """Read logs from the run_logs table via ORM."""
    try:
        from sqlalchemy import desc
        from ai_gateway.db.models import RunLog
        from ai_gateway.db.connection import get_session as _orm_session

        with _orm_session() as session:
            query = session.query(RunLog).filter(RunLog.run_id == run_id)

            if level:
                query = query.filter(RunLog.level == level.upper())
            if scene_id is not None:
                try:
                    query = query.filter(RunLog.scene_id == int(scene_id))
                except (ValueError, TypeError):
                    pass

            query = query.order_by(desc(RunLog.created_at)).limit(limit)
            rows = query.all()

            entries = [
                {
                    "id": r.id,
                    "run_id": r.run_id,
                    "scene_id": r.scene_id,
                    "step_tag": r.step_tag,
                    "level": r.level,
                    "message": r.message,
                    "extra_json": r.extra_json,
                    "loc": r.loc,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

        return jsonify({"run_id": run_id, "count": len(entries), "entries": entries})
    except Exception as e:
        _web_logger.error("Error querying run_logs via ORM: %s", e)
        # Fallback to raw file
        return _get_logs_from_file(run_id, level, scene_id, limit)


@app.route("/api/logs/stats")
def log_stats():
    """Return aggregate log statistics via ORM."""
    try:
        from sqlalchemy import func, desc
        from ai_gateway.db.models import RunLog
        from ai_gateway.db.connection import get_session as _orm_session

        with _orm_session() as session:
            # Count distinct runs
            runs_count = session.query(func.count(func.distinct(RunLog.run_id))).scalar() or 0
            total_entries = session.query(func.count(RunLog.id)).scalar() or 0

            # By level
            level_rows = (
                session.query(RunLog.level, func.count(RunLog.id).label("cnt"))
                .group_by(RunLog.level)
                .order_by(desc("cnt"))
                .all()
            )
            by_level = {r.level: r.cnt for r in level_rows}

            # Recent runs
            recent_rows = (
                session.query(
                    RunLog.run_id,
                    func.count(RunLog.id).label("cnt"),
                    func.min(RunLog.created_at).label("first_ts"),
                    func.max(RunLog.created_at).label("last_ts"),
                )
                .group_by(RunLog.run_id)
                .order_by(desc("last_ts"))
                .limit(10)
                .all()
            )
            recent_runs = [
                {
                    "run_id": r.run_id,
                    "cnt": r.cnt,
                    "first_ts": r.first_ts.isoformat() if r.first_ts else None,
                    "last_ts": r.last_ts.isoformat() if r.last_ts else None,
                }
                for r in recent_rows
            ]

        return jsonify({
            "runs": runs_count,
            "total_entries": total_entries,
            "by_level": by_level,
            "recent_runs": recent_runs,
        })
    except Exception as e:
        _web_logger.error("Error querying log stats: %s", e)
        return jsonify({"runs": 0, "total_entries": 0, "by_level": {}, "recent_runs": []})


# ── Image library API endpoints ─────────────────────────────────────────────

@app.route("/api/images")
def list_images():
    """Browse the image library with reuse statistics."""
    try:
        from sqlalchemy import desc
        from ai_gateway.db.models import ImageLibrary
        from ai_gateway.db.connection import get_session as _orm_session

        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)

        with _orm_session() as session:
            total = session.query(ImageLibrary).count()
            images = (
                session.query(ImageLibrary)
                .order_by(desc(ImageLibrary.created_at))
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )

            results = [
                {
                    "id": img.id,
                    "content_hash": img.content_hash,
                    "prompt": img.prompt[:200] if img.prompt else "",
                    "scene_desc": img.scene_desc,
                    "width": img.width,
                    "height": img.height,
                    "file_size": img.file_size,
                    "source": img.source,
                    "source_run_id": img.source_run_id,
                    "usage_count": img.usage_count,
                    "last_used_at": img.last_used_at.isoformat() if img.last_used_at else None,
                    "created_at": img.created_at.isoformat() if img.created_at else None,
                    "has_thumbnail": img.thumbnail_data is not None,
                }
                for img in images
            ]

        return jsonify({
            "total": total,
            "page": page,
            "per_page": per_page,
            "images": results,
        })
    except Exception as e:
        _web_logger.error("Error listing images: %s", e)
        return jsonify({"error": str(e), "total": 0, "images": []})


@app.route("/api/images/<int:image_id>/thumbnail")
def serve_thumbnail(image_id):
    """Serve a thumbnail JPEG from the image_library table."""
    try:
        from ai_gateway.db.models import ImageLibrary
        from ai_gateway.db.connection import get_session as _orm_session

        with _orm_session() as session:
            img = session.get(ImageLibrary, image_id)
            if img is None or img.thumbnail_data is None:
                return jsonify({"error": "Thumbnail not found"}), 404

            from flask import Response
            return Response(img.thumbnail_data, mimetype="image/jpeg")
    except Exception as e:
        _web_logger.error("Error serving thumbnail: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Background job runner ───────────────────────────────────────────────────

def _run_pipeline_job(job_id, context, do_research, do_web_search,
                      use_internet_image_search, fast_mode, language,
                      image_provider, video_provider, veo_direction_by_director):
    """Execute the pipeline in a background thread, updating job progress."""
    runtime = None
    try:
        _update_job(job_id, "running", 5, "Starting pipeline…")
        _web_logger.info("Job %s: Starting staged pipeline execution", job_id)

        # Deferred import so the web app can start even if some deps are missing
        from pipeline import run_pipeline, PipelineRuntime

        _update_job(job_id, "running", 10, "Research & planning…")

        # Generate run_id early so we can link it
        run_id = f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # Create the runtime with synchronisation primitives
        runtime = PipelineRuntime(run_id=run_id, job_id=job_id)
        _active_runtimes[job_id] = runtime

        result = run_pipeline(
            user_context=context,
            do_research=do_research,
            do_web_search=do_web_search,
            use_internet_image_search=use_internet_image_search,
            fast_mode=fast_mode,
            language=language,
            image_provider=image_provider,
            video_provider=video_provider,
            veo_direction_by_director=veo_direction_by_director,
            run_id=run_id,
            job_id=job_id,
            skip_review=False,
            runtime=runtime,
        )

        if result and os.path.exists(result):
            display_result = result
            output_marker = os.path.join("genai-pipeline", "output")
            if output_marker in result:
                display_result = result.split(output_marker, 1)[1].lstrip(os.sep).lstrip("/")
                display_result = f"genai-pipeline/output/{display_result}"
            _update_job(job_id, "completed", 100, "Video ready!",
                        result=result, display_path=display_result)
            _web_logger.info("Job %s: Completed successfully — %s", job_id, result)
        else:
            # Check if it was cancelled (not a true failure)
            current = _get_job_dict(job_id)
            if current and current.get("status") == "cancelled":
                _web_logger.info("Job %s: Was cancelled by user", job_id)
            else:
                _update_job(job_id, "failed", 100, "Pipeline finished but no video was produced.",
                            error="No output video generated.")
                _web_logger.warning("Job %s: Finished but no video produced", job_id)

    except Exception as exc:
        _update_job(job_id, "failed", 0, f"Error: {exc}", error=str(exc))
        _web_logger.error("Job %s: Failed — %s\n%s", job_id, exc, traceback.format_exc())
    finally:
        if runtime:
            _active_runtimes.pop(job_id, None)


def _get_job_dict(job_id: str):
    """Return the raw job dict from the database (used internally)."""
    from tools.db_utils import get_job as _gj
    return _gj(job_id)


def _update_job(job_id, status, progress, message, result=None, error=None, display_path=None):
    """Update job in the database (was previously in-memory dict)."""
    from tools.db_utils import update_job

    kwargs = {
        "status": status,
        "progress": progress,
        "message": message,
    }
    if error is not None:
        kwargs["error"] = error
    # Note: result and display_path are not stored in the jobs table;
    # they are passed through to the frontend via the run record instead.

    update_job(job_id, **kwargs)


def _parse_timestamp(dir_name: str) -> str:
    """Try to extract a readable datetime from a run_YYYYMMDD_HHMMSS name."""
    try:
        part = dir_name.replace("run_", "")
        dt = datetime.datetime.strptime(part[:15], "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return dir_name


# ── Module-level startup log ─────────────────────────────────────────────────
_startup_host = os.environ.get("WEB_HOST", "127.0.0.1")
_startup_port = int(os.environ.get("WEB_PORT", 5000))
_startup_debug = os.environ.get("WEB_DEBUG", "1") == "1"

_web_logger.info("=" * 60)
_web_logger.info("  Whiteboard Animation AI — Web UI")
_web_logger.info("  Listening on http://%s:%s", _startup_host, _startup_port)
_web_logger.info("  Debug mode: %s", _startup_debug)
_web_logger.info("  Logs directory: %s", _logs_dir)
_web_logger.info("=" * 60)


# ── Entry point (direct execution only) ─────────────────────────────────────

if __name__ == "__main__":
    app.run(host=_startup_host, port=_startup_port, debug=_startup_debug)
