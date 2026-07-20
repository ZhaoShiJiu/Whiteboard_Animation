"""
Doubao-Embedding-Vision — multimodal embedding provider via Volcengine ARK.

Supports text, image, video, and mixed multimodal inputs.
Returns a unified embedding vector suitable for cosine-similarity retrieval.

API reference: https://www.volcengine.com/docs/82379/1523520
"""

import base64
import os
import time
import uuid

import requests

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class DoubaoEmbeddingProvider(AbstractBaseProvider):
    """
    Adapter for Doubao-Embedding-Vision via Volcengine ARK.

    Calls ``POST /embeddings/multimodal``.  Each input element is an object:

    - Text:   ``{"type": "text", "text": "..."}``
    - Image:  ``{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}``
    - Video:  ``{"type": "video_url", "video_url": {"url": "..."}}``

    Optional knobs via *request.options*:
    - ``dimensions``: 1024 or 2048 (default 2048)
    - ``instructions``: inference prompt override (string)
    """

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self._api_key = os.getenv(config.get("api_key_env", ""))
        if not self._api_key:
            raise ValueError(
                f"ARK API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'ARK_API_KEY')}'."
            )
        self._endpoint = config.get(
            "endpoint", "https://ark.cn-beijing.volces.com/api/v3"
        )
        self._embeddings_path = config.get(
            "embeddings_path", "/embeddings/multimodal"
        )

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """
        Generate an embedding vector from text, image(s), video, or any mix.

        Args:
            request:
                - prompt: Text to embed (optional if images provided).
                - reference_images: Image bytes to embed (optional).
                - options:
                    - dimensions: 1024 or 2048 (default 2048).
                    - instructions: Optional inference prompt override.

        Returns:
            GatewayResponse with ``content`` as ``list[float]``.
        """
        request_id = getattr(request, "_request_id", None) or str(uuid.uuid4())
        start = time.time()

        # ---- Build multimodal input parts ------------------------------------
        input_parts: list[dict] = []

        if request.prompt:
            input_parts.append({"type": "text", "text": request.prompt})

        if request.reference_images:
            for img_bytes in request.reference_images:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                input_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })

        if not input_parts:
            raise ValueError(
                "DoubaoEmbeddingProvider.generate() requires at least "
                "a prompt (text) or reference_images (image bytes)."
            )

        # ---- Build payload ---------------------------------------------------
        dimensions = request.options.get("dimensions", 2048)
        if dimensions not in (1024, 2048):
            dimensions = 2048

        payload: dict = {
            "model": self.model,
            "input": input_parts,
            "dimensions": dimensions,
            "encoding_format": "float",
        }

        # Optional inference prompt override
        instructions = request.options.get("instructions")
        if instructions:
            payload["instructions"] = instructions

        # ---- Call ARK multimodal embeddings API -------------------------------
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        url = f"{self._endpoint}{self._embeddings_path}"

        try:
            http_resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"Doubao embedding request failed: {exc}"
            ) from exc

        if http_resp.status_code != 200:
            raise RuntimeError(
                f"Doubao embedding error (status={http_resp.status_code}): "
                f"{http_resp.text[:500]}"
            )

        data = http_resp.json()
        latency = (time.time() - start) * 1000

        # ---- Extract embedding vector ----------------------------------------
        # Response shape: {"data": {"embedding": [...]}, ...}
        try:
            embedding_data = data["data"]
            if isinstance(embedding_data, list):
                vector = embedding_data[0]["embedding"]
            else:
                vector = embedding_data["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected Doubao embedding response structure: "
                f"{str(data)[:300]}"
            ) from exc

        # ---- Build usage (use detailed token breakdown when available) --------
        usage = UsageStats()
        if "usage" in data:
            usage.input_tokens = data["usage"].get("prompt_tokens", 0)
            usage.output_tokens = data["usage"].get("total_tokens", 0)

            # Per-modality token breakdown
            details = data["usage"].get("prompt_tokens_details", {})
            if details:
                text_tokens = details.get("text_tokens", 0)
                image_tokens = details.get("image_tokens", 0)
                # Store image token count for cost calc
                if image_tokens > 0:
                    usage.images = len(request.reference_images) if request.reference_images else 0

        usage.cost = self.calculate_cost(usage)

        return GatewayResponse(
            request_id=request_id,
            task=request.task,
            provider=self.name,
            model=self.model,
            content=vector,
            usage=usage,
            latency_ms=latency,
            raw_response=data,
        )

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        """
        Estimate cost based on text tokens + image count.

        Pricing is approximate — update once official pricing is published.
        """
        text_cost = (usage.input_tokens / 1000.0) * 0.0005
        image_cost = usage.images * 0.0005
        return round(text_cost + image_cost, 6)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable_markers = [
            "429", "rate limit",
            "500", "502", "503", "504",
            "timeout", "connection", "reset by peer",
            "service unavailable",
        ]
        return any(marker in error_str for marker in retryable_markers)
