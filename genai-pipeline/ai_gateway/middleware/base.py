"""
Middleware base class with before/after hooks.

Middleware wraps the provider.generate() call — before() runs on the way in,
after() runs on the way out (success or failure).  The gateway calls before()
in registration order and after() in reverse order, so the chain is:

    Logging.before → Cost.before → Retry.before → Provider.generate()
    → Retry.after → Cost.after → Logging.after
"""

from ..models import GatewayRequest, GatewayResponse


class BaseMiddleware:
    """Pluggable middleware with before/after hooks."""

    def before(self, request: GatewayRequest) -> None:
        """Called BEFORE the provider processes the request."""

    def after(
        self,
        request: GatewayRequest,
        response: GatewayResponse | None,
        error: Exception | None,
    ) -> None:
        """Called AFTER the provider responds (or raises)."""
