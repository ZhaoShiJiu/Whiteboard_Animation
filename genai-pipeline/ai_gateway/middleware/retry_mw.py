"""
RetryMiddleware — exponential-backoff retry for transient failures.
"""

import logging
import time
from typing import Optional

from ..models import GatewayRequest, GatewayResponse
from ..providers.base import AbstractBaseProvider
from .base import BaseMiddleware


class RetryMiddleware(BaseMiddleware):
    """
    Wraps the provider.generate() call with exponential-backoff retries.

    Configuration is read from gateway.yaml's ``retry`` section:

        retry:
          max_retries: 3
          backoff: exponential     # exponential | fixed | linear
          initial_delay_seconds: 5
          max_delay_seconds: 60
    """

    def __init__(
        self,
        retry_config: dict,
        logger: Optional[logging.Logger] = None,
    ):
        self._max_retries: int = retry_config.get("max_retries", 3)
        self._backoff: str = retry_config.get("backoff", "exponential")
        self._initial_delay: float = float(retry_config.get("initial_delay_seconds", 5))
        self._max_delay: float = float(retry_config.get("max_delay_seconds", 60))
        self._logger = logger or logging.getLogger("ai_gateway.retry")

    def execute_with_retry(
        self,
        provider: AbstractBaseProvider,
        request: GatewayRequest,
    ) -> GatewayResponse:
        """
        Call provider.generate() with retry logic.

        Raises the last error if all retries are exhausted.
        """
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return provider.generate(request)
            except Exception as exc:
                last_error = exc
                if not provider.is_retryable(exc):
                    self._logger.warning(
                        "Non-retryable error from %s: %s", provider.name, exc
                    )
                    raise  # non-retryable error — fail fast
                if attempt == self._max_retries:
                    self._logger.error(
                        "%s failed after %d attempts: %s",
                        provider.name, self._max_retries, exc,
                    )
                    raise  # exhausted retries

                delay = self._calc_delay(attempt)
                self._logger.warning(
                    "%s failed (attempt %d/%d): %s — retrying in %.0fs",
                    provider.name, attempt, self._max_retries, exc, delay,
                )
                time.sleep(delay)

        # Should never reach here, but satisfy type checker
        raise last_error  # type: ignore[misc]

    def _calc_delay(self, attempt: int) -> float:
        """Calculate delay for the given attempt number."""
        if self._backoff == "exponential":
            delay = self._initial_delay * (2 ** (attempt - 1))
        elif self._backoff == "linear":
            delay = self._initial_delay * attempt
        else:  # fixed
            delay = self._initial_delay
        return min(delay, self._max_delay)
