"""
SQLAlchemy ORM models for AI Gateway observability.
"""

import uuid
from datetime import datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AiRequestLog(Base):
    """
    Every Gateway request is logged here — task, provider, model, latency, status.

    Table: ai_request_logs
    """

    __tablename__ = "ai_request_logs"

    id:         Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))
    task:       Mapped[str]              # story | image | voice | video
    provider:   Mapped[str]              # deepseek | qwen | minimax | seedance
    model:      Mapped[str]              # deepseek-v4-pro | ...
    status:     Mapped[str]              # success | failed | timeout
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<AiRequestLog id={self.id} task={self.task} "
            f"provider={self.provider} status={self.status} latency={self.latency_ms}ms>"
        )


class AiUsage(Base):
    """
    Detailed usage & cost breakdown — one row per Gateway request.

    Table: ai_usage
    """

    __tablename__ = "ai_usage"

    request_id:    Mapped[str] = mapped_column(primary_key=True)
    type:          Mapped[str]            # llm | image | tts | video
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
