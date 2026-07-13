"""
LoggingMiddleware — records every Gateway call to ai_request_logs.
"""

import time
import uuid

from sqlalchemy.orm import Session

from ..db import AiRequestLog, get_session
from ..models import GatewayRequest, GatewayResponse
from .base import BaseMiddleware


class LoggingMiddleware(BaseMiddleware):
    """Persists request metadata to ai_request_logs."""

    def before(self, request: GatewayRequest) -> None:
        # Attach tracking fields directly to the request object
        request._start_time = time.time()          # type: ignore[attr-defined]
        request._request_id = str(uuid.uuid4())    # type: ignore[attr-defined]

    def after(
        self,
        request: GatewayRequest,
        response: GatewayResponse | None,
        error: Exception | None,
    ) -> None:
        start_time: float = getattr(request, "_start_time", time.time())
        latency_ms = int((time.time() - start_time) * 1000)

        provider = response.provider if response else "unknown"
        model = response.model if response else "unknown"
        status = "success" if error is None else ("timeout" if isinstance(error, TimeoutError) else "failed")

        log_entry = AiRequestLog(
            id=getattr(request, "_request_id", str(uuid.uuid4())),
            task=request.task,
            provider=provider,
            model=model,
            status=status,
            latency_ms=latency_ms,
        )

        try:
            with get_session() as session:
                session.add(log_entry)
        except RuntimeError:
            # DB not initialised — silently skip logging
            pass
