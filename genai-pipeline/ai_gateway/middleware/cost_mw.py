"""
CostMiddleware — records usage & cost to ai_usage.
"""

from ..db import AiUsage, get_session
from ..models import GatewayRequest, GatewayResponse
from ..providers.registry import PROVIDER_TYPE_MAP
from .base import BaseMiddleware


class CostMiddleware(BaseMiddleware):
    """Persists usage and cost data to ai_usage."""

    def after(
        self,
        request: GatewayRequest,
        response: GatewayResponse | None,
        error: Exception | None,
    ) -> None:
        if response is None or error is not None:
            return  # nothing to record

        usage = response.usage
        usage_type = PROVIDER_TYPE_MAP.get(response.provider, "llm")

        usage_entry = AiUsage(
            request_id=response.request_id,
            type=usage_type,
            input_tokens=usage.input_tokens if usage.input_tokens > 0 else None,
            output_tokens=usage.output_tokens if usage.output_tokens > 0 else None,
            images=usage.images if usage.images > 0 else None,
            characters=usage.characters if usage.characters > 0 else None,
            duration=usage.duration if usage.duration > 0.0 else None,
            resolution=usage.resolution if usage.resolution else None,
            cost=usage.cost,
        )

        try:
            with get_session() as session:
                session.add(usage_entry)
        except RuntimeError:
            # DB not initialised — silently skip
            pass
