"""
SQLAlchemy ORM models for Whiteboard Animation AI.

Covers:
- AI Gateway observability (ai_request_logs, ai_usage) — existing
- Pipeline run logs (run_logs) — replaces raw sqlite3 in log_utils.py
- Business tables (jobs, runs, scenes)
- Image retrieval (image_library)
- Media assets (media_assets)
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Existing tables (AI Gateway observability)
# ═══════════════════════════════════════════════════════════════════════════════


class AiRequestLog(Base):
    """
    Every Gateway request is logged here — task, provider, model, latency, status.
    """

    __tablename__ = "ai_request_logs"

    id:         Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))
    task:       Mapped[str]              # story | image | voice | video
    provider:   Mapped[str]              # deepseek | qwen | minimax | seedance
    model:      Mapped[str]              # deepseek-v4-pro | ...
    status:     Mapped[str]              # success | failed | timeout
    latency_ms: Mapped[int]
    # -- New fields for business-table linkage --
    run_id:     Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    scene_id:   Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<AiRequestLog id={self.id} task={self.task} "
            f"provider={self.provider} status={self.status} latency={self.latency_ms}ms>"
        )


class AiUsage(Base):
    """
    Detailed usage & cost breakdown — one row per Gateway request.
    """

    __tablename__ = "ai_usage"

    request_id:    Mapped[str] = mapped_column(primary_key=True)
    type:          Mapped[str]            # llm | image | tts | video | embedding | search
    input_tokens:  Mapped[int | None] = mapped_column(nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    images:        Mapped[int | None] = mapped_column(nullable=True)
    characters:    Mapped[int | None] = mapped_column(nullable=True)
    duration:      Mapped[float | None] = mapped_column(nullable=True)
    resolution:    Mapped[str | None] = mapped_column(nullable=True)
    cost:          Mapped[float] = mapped_column(default=0.0)

    def __repr__(self) -> str:
        return (
            f"<AiUsage request_id={self.request_id} type={self.type} cost={self.cost}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline run logs (replaces raw sqlite3 in log_utils.py DBLogHandler)
# ═══════════════════════════════════════════════════════════════════════════════


class RunLog(Base):
    """
    Structured log entries for each pipeline run.

    Previously created via raw ``CREATE TABLE IF NOT EXISTS run_logs`` in
    ``log_utils.DBLogHandler._worker()``.  Now managed through the ORM.
    """

    __tablename__ = "run_logs"

    id:         Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id:     Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    scene_id:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_tag:   Mapped[str | None] = mapped_column(String, nullable=True)
    level:      Mapped[str]              # DEBUG | INFO | WARNING | ERROR | CRITICAL
    message:    Mapped[str]
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    loc:        Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<RunLog id={self.id} run={self.run_id} level={self.level}>"


# ═══════════════════════════════════════════════════════════════════════════════
# Core business tables
# ═══════════════════════════════════════════════════════════════════════════════


class Job(Base):
    """
    A user-submitted pipeline job — replaces the in-memory ``jobs: dict``.
    """

    __tablename__ = "jobs"

    id:            Mapped[str] = mapped_column(String(12), primary_key=True)
    status:        Mapped[str] = mapped_column(String(20), default="queued")
    progress:      Mapped[int] = mapped_column(Integer, default=0)
    message:       Mapped[str | None] = mapped_column(String(500), nullable=True)
    context:       Mapped[str] = mapped_column(Text, default="")
    language:      Mapped[str] = mapped_column(String(20), default="english")
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error:         Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id:        Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                     onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "context": self.context,
            "language": self.language,
            "run_id": self.run_id,
            "video_provider": (self.settings_json or {}).get("video_provider"),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "result": None,
            "error": self.error,
        }


class Run(Base):
    """
    One pipeline run — contains everything produced by a single job execution.
    """

    __tablename__ = "runs"

    id:               Mapped[str] = mapped_column(String(50), primary_key=True)
    job_id:           Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    status:           Mapped[str] = mapped_column(String(20), default="running")
    context:          Mapped[str | None] = mapped_column(Text, nullable=True)
    language:         Mapped[str] = mapped_column(String(20), default="english")
    settings_json:    Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scene_count:      Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_sec:     Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_total:       Mapped[float | None] = mapped_column(Float, nullable=True)
    output_dir:       Mapped[str | None] = mapped_column(String(500), nullable=True)
    final_video:      Mapped[str | None] = mapped_column(String(500), nullable=True)
    final_srt:        Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_plan_json:  Mapped[dict | None] = mapped_column(JSON, nullable=True)
    research_report:  Mapped[str | None] = mapped_column(Text, nullable=True)
    error:            Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at:     Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Scene(Base):
    """
    One scene within a pipeline run — the foundational unit of content.
    """

    __tablename__ = "scenes"

    id:                Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id:            Mapped[str] = mapped_column(String(50), index=True)
    scene_index:       Mapped[int] = mapped_column(Integer)
    narration:         Mapped[str | None] = mapped_column(Text, nullable=True)
    refined_narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    description:       Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_setup:      Mapped[str | None] = mapped_column(Text, nullable=True)
    image_prompt:      Mapped[str | None] = mapped_column(Text, nullable=True)
    text_overlay:      Mapped[str | None] = mapped_column(Text, nullable=True)
    image_id:          Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_path:        Mapped[str | None] = mapped_column(String(500), nullable=True)
    srt_content:       Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_sec:      Mapped[float | None] = mapped_column(Float, nullable=True)
    cost:              Mapped[float] = mapped_column(Float, default=0.0)
    status:            Mapped[str] = mapped_column(String(20), default="pending")
    error:             Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Image library — retrieval & reuse
# ═══════════════════════════════════════════════════════════════════════════════


class ImageLibrary(Base):
    """
    Stores generated/reference images with embeddings for semantic retrieval.

    Images are stored as BLOBs in SQLite (typical size 1-5 MB each).
    At 500 images × 3 MB = 1.5 GB — well within SQLite's capability.
    """

    __tablename__ = "image_library"

    id:             Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content_hash:   Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_data:      Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    thumbnail_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    prompt:         Mapped[str] = mapped_column(Text)
    scene_desc:     Mapped[str | None] = mapped_column(Text, nullable=True)
    width:          Mapped[int] = mapped_column(Integer, default=1920)
    height:         Mapped[int] = mapped_column(Integer, default=1080)
    file_size:      Mapped[int] = mapped_column(Integer, default=0)
    source:         Mapped[str] = mapped_column(String(20), default="ai_gen")
    source_run_id:  Mapped[str | None] = mapped_column(String(50), nullable=True)
    usage_count:    Mapped[int] = mapped_column(Integer, default=1)
    last_used_at:   Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Media assets — general-purpose file tracking
# ═══════════════════════════════════════════════════════════════════════════════


class MediaAsset(Base):
    """
    Tracks generated media files (audio, video, SRT, animation intermediates).

    Small text content (SRT, JSON, MD) may be stored inline in ``content_text``;
    large binary assets are referenced by ``file_path`` on the filesystem.
    """

    __tablename__ = "media_assets"

    id:           Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id:       Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    scene_id:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_type:   Mapped[str] = mapped_column(String(20))  # video | audio | srt | animation | plan_json
    file_path:    Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_name:    Mapped[str | None] = mapped_column(String(200), nullable=True)
    file_size:    Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type:    Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
