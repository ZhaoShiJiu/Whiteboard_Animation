"""
AI Gateway — single entry point for all AI model calls.

Wire everything together: load config → init DB → create providers →
assemble middleware chain → expose generate().

Usage::

    from ai_gateway import generate

    # LLM call
    resp = generate(task="story", prompt="Write a story about...",
                    options={"response_format": "json"})
    print(resp.content)

    # Image generation
    img = generate(task="image", prompt="A whiteboard sketch of a rocket",
                   reference_images=[previous_frame_bytes],
                   options={"aspect_ratio": "16:9"})
"""

import logging
import os
from pathlib import Path
from typing import Optional

from .config_loader import load_config
from .db.connection import init_db
from .middleware import CostMiddleware, LoggingMiddleware, RetryMiddleware
from .models import GatewayRequest, GatewayResponse, UsageStats
from .providers.base import AbstractBaseProvider
from .providers.registry import create_provider

# Internal logger for the gateway module itself
_gw_logger = logging.getLogger("ai_gateway")


class Gateway:
    """Central gateway that routes tasks to providers through a middleware chain."""

    def __init__(self, config_path: Optional[str] = None, logger: Optional[logging.Logger] = None):
        """
        Args:
            config_path: Path to gateway.yaml. Defaults to the one next to this file.
            logger: Optional Python logger. Falls back to module-level ``ai_gateway`` logger.
        """
        self._logger = logger or _gw_logger

        if config_path is None:
            config_path = str(Path(__file__).parent / "gateway.yaml")

        self.config = load_config(config_path, logger=self._logger)

        # ---- Initialise database -------------------------------------------
        try:
            init_db(self.config["database"])
        except Exception as e:
            self._logger.warning(
                "Database init failed (%s). Logging and cost tracking will be skipped.", e
            )

        # ---- Create providers ----------------------------------------------
        self._providers: dict[str, AbstractBaseProvider] = {}
        for name, provider_cfg in self.config["providers"].items():
            try:
                self._providers[name] = create_provider(name, provider_cfg)
            except Exception as e:
                self._logger.warning(
                    "Failed to initialise provider '%s': %s", name, e
                )

        # ---- Assemble middleware chain -------------------------------------
        log_config = self.config.get("logging", {})
        self._middlewares = [
            LoggingMiddleware(),
            CostMiddleware(),
        ]
        self._retry_mw = RetryMiddleware(self.config["retry"], logger=self._logger)

        self._logger.info(
            "AI Gateway initialised with providers: %s", list(self._providers.keys())
        )
        self._logger.info(
            "AI Gateway routes: %s", list(self.config['routes'].keys())
        )

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """
        Process a request through the full middleware chain → provider.

        Args:
            request: A GatewayRequest specifying task, prompt, and optional params.

        Returns:
            GatewayResponse with content, usage, and metadata.

        Raises:
            ValueError: If the task is unknown or the provider is not available.
            RuntimeError: If the provider API returns an error after retries.
        """
        # ---- Route to provider ---------------------------------------------
        # Allow runtime provider override via options.provider
        provider = self._resolve_provider(request)

        # ---- before() hooks ------------------------------------------------
        for mw in self._middlewares:
            mw.before(request)

        # ---- Execute (with retry) ------------------------------------------
        response: Optional[GatewayResponse] = None
        error: Optional[Exception] = None

        try:
            response = self._retry_mw.execute_with_retry(provider, request)
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            # ---- after() hooks (reverse order) -------------------------------
            for mw in reversed(self._middlewares):
                try:
                    mw.after(request, response, error)
                except Exception:
                    pass  # never let middleware errors propagate

    # ------------------------------------------------------------------
    def _resolve_provider(self, request: GatewayRequest) -> AbstractBaseProvider:
        """
        Resolve the provider for a request.

        Checks request.options.provider first for a direct provider name override,
        then falls back to the configured task→provider route mapping.
        """
        # Runtime provider override (e.g. options={"provider": "happyhorse"})
        provider_override = request.options.get("provider")
        if provider_override:
            provider = self._providers.get(provider_override)
            if provider is not None:
                self._logger.debug(
                    "Using provider override '%s' for task '%s'",
                    provider_override, request.task,
                )
                return provider
            raise RuntimeError(
                f"Provider override '{provider_override}' not found. "
                f"Available: {list(self._providers.keys())}"
            )

        # Standard task→provider routing
        return self._route(request.task)

    # ------------------------------------------------------------------
    def _route(self, task: str) -> AbstractBaseProvider:
        """Look up the provider registered for the given task."""
        route_cfg = self.config["routes"].get(task)
        if route_cfg is None:
            raise ValueError(
                f"Unknown task: '{task}'. "
                f"Known tasks: {list(self.config['routes'].keys())}"
            )
        provider_name = route_cfg["provider"]
        provider = self._providers.get(provider_name)
        if provider is None:
            raise RuntimeError(
                f"Provider '{provider_name}' (task='{task}') is not initialised. "
                f"Check gateway.yaml and your API keys."
            )
        return provider


# ------------------------------------------------------------------
# Module-level singleton — initialised lazily on first generate() call
# ------------------------------------------------------------------
_gateway: Optional[Gateway] = None


def _get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        _gateway = Gateway()
    return _gateway


def generate(
    task: str,
    prompt: str,
    reference_images: Optional[list[bytes]] = None,
    options: Optional[dict] = None,
) -> GatewayResponse:
    """
    Convenience function — the primary API for all tool modules.

    Args:
        task: One of "story", "search", "image", "voice", "video".
        prompt: The primary text prompt.
        reference_images: Optional image bytes for image/video providers.
        options: Provider-specific knobs (language, aspect_ratio, duration, …).

    Returns:
        GatewayResponse with content, usage, and metadata.

    Example::

        resp = generate("story", "Tell me a story", options={"response_format": "json"})
        img  = generate("image", "A whiteboard sketch of a cat")
        tts  = generate("voice", "Hello world", options={"voice_id": "male-qn-qingse"})
        vid  = generate("video", "A rocket launching", options={"duration": 8})
        sr   = generate("search", "最新科技新闻")
    """
    gw = _get_gateway()
    request = GatewayRequest(
        task=task,
        prompt=prompt,
        reference_images=reference_images or [],
        options=options or {},
    )
    return gw.generate(request)
