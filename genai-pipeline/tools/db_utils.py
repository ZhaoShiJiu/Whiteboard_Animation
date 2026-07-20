"""
Database utility helpers for pipeline and web-app integration.

Thin wrappers around SQLAlchemy ORM models.  Every function handles its own
session lifecycle and fails gracefully if the database is not initialised.

Usage::

    from tools.db_utils import create_job, create_run, update_run, create_scene

    create_job("abc123", context="topic", language="english", settings={})
    create_run("run_20260715_143022", job_id="abc123", ...)
    update_run("run_20260715_143022", status="completed", cost_total=1.23)
"""

import datetime
import logging
import os
from pathlib import Path
from typing import Any, Optional

_logger = logging.getLogger("db_utils")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_db_path() -> Optional[str]:
    """Resolve the ai_gateway.db path so it can be opened even if the
    global ORM engine hasn't been initialised yet."""
    db_path = os.environ.get("AI_GATEWAY_DB_PATH")
    if db_path and os.path.isfile(db_path):
        return db_path

    # Try gateway.yaml resolution
    try:
        import yaml
        gateway_yaml = Path(__file__).resolve().parent.parent / "ai_gateway" / "gateway.yaml"
        if gateway_yaml.exists():
            with open(gateway_yaml, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            db_cfg = raw.get("database", {})
            db_path = db_cfg.get("path", "ai_gateway.db")
            if not os.path.isabs(db_path):
                db_path = str(gateway_yaml.parent / db_path)
            return db_path
    except Exception:
        pass

    return None


def _get_session():
    """Return a context-manager yielding a SQLAlchemy Session.

    Uses the global ORM session if the engine has been initialised;
    otherwise creates a local engine to the same DB file so that
    ``db_utils`` works even before ``Gateway.__init__`` has run.
    """
    # Check whether the global engine is ready — this is the reliable test,
    # because ``get_session()`` is a @contextmanager whose body (and
    # RuntimeError) doesn't execute until ``__enter__``, which is too late
    # for a try/except around the call site.
    try:
        from ai_gateway.db.connection import _engine as _global_engine
    except ImportError:
        _global_engine = None

    if _global_engine is not None:
        from ai_gateway.db.connection import get_session as _global
        return _global()

    # -- Fallback: create a local engine to the same DB file ------------------
    from contextlib import contextmanager
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    db_path = _ensure_db_path()
    if not db_path:
        raise RuntimeError("Cannot resolve database path.")

    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @contextmanager
    def _local_session():
        session = Session(bind=engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _local_session()


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------


def create_job(
    job_id: str,
    context: str = "",
    language: str = "english",
    settings: Optional[dict] = None,
) -> None:
    """Create a new job record."""
    from ai_gateway.db.models import Job

    try:
        with _get_session() as session:
            job = Job(
                id=job_id,
                status="queued",
                progress=0,
                message="Job queued…",
                context=context,
                language=language,
                settings_json=settings or {},
            )
            session.add(job)
    except Exception as exc:
        _logger.warning("create_job failed: %s", exc)


def update_job(job_id: str, **kwargs: Any) -> None:
    """Update fields on an existing job.  ``kwargs`` are column names → values."""
    from ai_gateway.db.models import Job

    try:
        with _get_session() as session:
            job = session.get(Job, job_id)
            if job is None:
                _logger.debug("update_job: job %s not found", job_id)
                return
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            if "updated_at" not in kwargs:
                job.updated_at = datetime.datetime.utcnow()
    except Exception as exc:
        _logger.warning("update_job(%s) failed: %s", job_id, exc)


def get_job(job_id: str) -> Optional[dict]:
    """Return a job as a dict, or None."""
    from ai_gateway.db.models import Job

    try:
        with _get_session() as session:
            job = session.get(Job, job_id)
            return job.to_dict() if job else None
    except Exception as exc:
        _logger.warning("get_job(%s) failed: %s", job_id, exc)
        return None


def list_jobs(limit: int = 100) -> list[dict]:
    """Return recent jobs as dicts, newest first."""
    from ai_gateway.db.models import Job

    try:
        with _get_session() as session:
            from sqlalchemy import desc

            jobs = (
                session.query(Job)
                .order_by(desc(Job.created_at))
                .limit(limit)
                .all()
            )
            return [j.to_dict() for j in jobs]
    except Exception as exc:
        _logger.warning("list_jobs failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------


def create_run(
    run_id: str,
    job_id: Optional[str] = None,
    context: str = "",
    language: str = "english",
    settings: Optional[dict] = None,
    output_dir: str = "",
) -> None:
    """Create a new pipeline run record."""
    from ai_gateway.db.models import Run

    try:
        with _get_session() as session:
            run = Run(
                id=run_id,
                job_id=job_id,
                status="running",
                context=context,
                language=language,
                settings_json=settings or {},
                output_dir=output_dir,
            )
            session.add(run)
    except Exception as exc:
        _logger.warning("create_run failed: %s", exc)


def update_run(run_id: str, **kwargs: Any) -> None:
    """Update fields on an existing run."""
    from ai_gateway.db.models import Run

    try:
        with _get_session() as session:
            run = session.get(Run, run_id)
            if run is None:
                _logger.debug("update_run: run %s not found", run_id)
                return
            for key, value in kwargs.items():
                if hasattr(run, key):
                    setattr(run, key, value)
    except Exception as exc:
        _logger.warning("update_run(%s) failed: %s", run_id, exc)


def list_runs(limit: int = 50) -> list[dict]:
    """Return recent runs with summary info, newest first."""
    from ai_gateway.db.models import Run

    try:
        with _get_session() as session:
            from sqlalchemy import desc

            runs = (
                session.query(Run)
                .order_by(desc(Run.created_at))
                .limit(limit)
                .all()
            )
            results = []
            for r in runs:
                created_ts = r.created_at.isoformat() if r.created_at else ""
                run_dir_name = r.id if r.id else ""
                try:
                    # Try to extract a readable timestamp from run_id
                    part = run_dir_name.replace("run_", "")
                    dt = datetime.datetime.strptime(part[:15], "%Y%m%d_%H%M%S")
                    created_ts = dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, IndexError):
                    pass

                results.append({
                    "run_id": r.id,
                    "created_at": created_ts,
                    "has_final_video": bool(r.final_video),
                    "video_name": r.final_video.replace("\\", "/").rsplit("/", 1)[-1] if r.final_video else None,
                    "scene_count": r.scene_count or 0,
                    "has_log": True,  # logs are in DB
                    "status": r.status,
                    "context": r.context,
                    "language": r.language,
                    "cost_total": r.cost_total,
                    "duration_sec": r.duration_sec,
                })
            return results
    except Exception as exc:
        _logger.warning("list_runs failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Scene helpers
# ---------------------------------------------------------------------------


def create_scene(
    run_id: str,
    scene_index: int,
    narration: str = "",
    description: str = "",
    visual_setup: str = "",
    text_overlay: str = "",
) -> int:
    """Create a new scene record.  Returns the scene's auto-increment id."""
    from ai_gateway.db.models import Scene

    try:
        with _get_session() as session:
            scene = Scene(
                run_id=run_id,
                scene_index=scene_index,
                narration=narration,
                description=description,
                visual_setup=visual_setup,
                text_overlay=text_overlay,
                status="pending",
            )
            session.add(scene)
            session.flush()  # populate scene.id
            return scene.id
    except Exception as exc:
        _logger.warning("create_scene failed: %s", exc)
        return -1


def update_scene(scene_id: int, **kwargs: Any) -> None:
    """Update fields on an existing scene."""
    from ai_gateway.db.models import Scene

    try:
        with _get_session() as session:
            scene = session.get(Scene, scene_id)
            if scene is None:
                _logger.debug("update_scene: scene %s not found", scene_id)
                return
            for key, value in kwargs.items():
                if hasattr(scene, key):
                    setattr(scene, key, value)
    except Exception as exc:
        _logger.warning("update_scene(%s) failed: %s", scene_id, exc)


# ---------------------------------------------------------------------------
# Media Asset helpers
# ---------------------------------------------------------------------------


def create_media_asset(
    run_id: str,
    asset_type: str,
    file_path: str = "",
    scene_id: Optional[int] = None,
    file_name: str = "",
    file_size: int = 0,
    mime_type: str = "",
    content_text: Optional[str] = None,
    is_temporary: bool = False,
) -> None:
    """Record a media asset (audio, video, SRT, plan JSON, etc.)."""
    from ai_gateway.db.models import MediaAsset

    try:
        with _get_session() as session:
            asset = MediaAsset(
                run_id=run_id,
                scene_id=scene_id,
                asset_type=asset_type,
                file_path=file_path,
                file_name=file_name or os.path.basename(file_path) if file_path else "",
                file_size=file_size,
                mime_type=mime_type,
                content_text=content_text,
                is_temporary=is_temporary,
            )
            session.add(asset)
    except Exception as exc:
        _logger.warning("create_media_asset failed: %s", exc)


def delete_temporary_assets(run_id: str) -> int:
    """Delete temporary media asset records for a run.  Returns count deleted."""
    from ai_gateway.db.models import MediaAsset

    try:
        with _get_session() as session:
            count = (
                session.query(MediaAsset)
                .filter(MediaAsset.run_id == run_id, MediaAsset.is_temporary.is_(True))
                .delete()
            )
            return count
    except Exception as exc:
        _logger.warning("delete_temporary_assets failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------


def get_cost_summary() -> dict:
    """Return aggregate cost data from the ai_usage table."""
    from ai_gateway.db.models import AiUsage

    try:
        with _get_session() as session:
            from sqlalchemy import func

            total_requests = session.query(func.count(AiUsage.request_id)).scalar() or 0
            total_cost = session.query(func.sum(AiUsage.cost)).scalar() or 0.0

            # By type — query AiUsage directly (no join needed, works for all data)
            by_provider_rows = (
                session.query(
                    AiUsage.type.label("provider"),
                    func.count(AiUsage.request_id).label("cnt"),
                    func.sum(AiUsage.cost).label("total_cost"),
                )
                .group_by(AiUsage.type)
                .order_by(func.sum(AiUsage.cost).desc())
                .all()
            )
            by_provider = [
                {"provider": r.provider, "cnt": r.cnt, "total_cost": round(r.total_cost or 0, 6)}
                for r in by_provider_rows
            ]

            # Recent — join AiUsage to get cost & token data
            from ai_gateway.db.models import AiRequestLog

            recent_rows = (
                session.query(
                    AiRequestLog,
                    AiUsage.cost,
                    AiUsage.input_tokens,
                    AiUsage.output_tokens,
                    AiUsage.type.label("usage_type"),
                )
                .outerjoin(AiUsage, AiUsage.request_id == AiRequestLog.id)
                .order_by(AiRequestLog.created_at.desc())
                .limit(20)
                .all()
            )
            recent = [
                {
                    "id": r.id,
                    "task": r.task,
                    "provider": r.provider,
                    "model": r.model,
                    "status": r.status,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "cost": round(cost or 0, 6),
                    "input_tokens": input_tokens or 0,
                    "output_tokens": output_tokens or 0,
                    "total_tokens": (input_tokens or 0) + (output_tokens or 0),
                    "usage_type": usage_type,
                }
                for r, cost, input_tokens, output_tokens, usage_type in recent_rows
            ]

            return {
                "total_cost": round(total_cost, 6),
                "total_requests": total_requests,
                "by_provider": by_provider,
                "recent": recent,
            }
    except Exception as exc:
        _logger.warning("get_cost_summary failed: %s", exc)
        return {"total_cost": 0, "total_requests": 0, "by_provider": [], "recent": []}
