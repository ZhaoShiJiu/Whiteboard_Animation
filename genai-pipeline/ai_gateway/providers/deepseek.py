"""
DeepSeek V4 Pro — LLM reasoning provider (OpenAI-compatible protocol).
"""

import os
import time
import uuid

from openai import OpenAI

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class DeepSeekProvider(AbstractBaseProvider):
    """Adapter for DeepSeek V4 Pro via the OpenAI-compatible chat/completions endpoint."""

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        api_key = os.getenv(config.get("api_key_env", ""))
        if not api_key:
            raise ValueError(
                f"DeepSeek API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'DEEPSEEK_API_KEY')}'."
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=config.get("endpoint", "https://api.deepseek.com/v1"),
            timeout=float(self.timeout),
        )

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        request_id = getattr(request, "_request_id", None) or str(uuid.uuid4())
        start = time.time()

        messages = [{"role": "user", "content": request.prompt}]

        # ---- Build kwargs --------------------------------------------------
        kwargs: dict = {"model": self.model, "messages": messages}

        # JSON mode (maps to the Director's response_mime_type usage)
        if request.options.get("response_format") == "json":
            kwargs["response_format"] = {"type": "json_object"}

        # Max tokens
        if "max_tokens" in request.options:
            kwargs["max_tokens"] = request.options["max_tokens"]

        # Temperature
        if "temperature" in request.options:
            kwargs["temperature"] = request.options["temperature"]

        # ---- Call API ------------------------------------------------------
        raw = self._client.chat.completions.create(**kwargs)

        latency = (time.time() - start) * 1000
        choice = raw.choices[0]

        content = choice.message.content or ""

        # ---- Build usage ---------------------------------------------------
        usage = UsageStats()
        if raw.usage:
            usage.input_tokens = raw.usage.prompt_tokens or 0
            usage.output_tokens = raw.usage.completion_tokens or 0
        usage.cost = self.calculate_cost(usage)

        return GatewayResponse(
            request_id=request_id,
            task=request.task,
            provider=self.name,
            model=self.model,
            content=content,
            usage=usage,
            latency_ms=latency,
            raw_response=raw,
        )

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        input_cost = (usage.input_tokens / 1000.0) * 0.001
        output_cost = (usage.output_tokens / 1000.0) * 0.002
        return round(input_cost + output_cost, 6)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        # OpenAI SDK wraps HTTP errors; check for transient status codes & network issues
        retryable_markers = [
            "429", "rate limit",
            "500", "502", "503", "504",
            "timeout", "connection", "reset by peer",
            "service unavailable",
        ]
        return any(marker in error_str for marker in retryable_markers)
