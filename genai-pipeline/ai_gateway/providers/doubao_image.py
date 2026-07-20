"""
Doubao-Seedream-4.5 — image generation provider via Volcengine ARK.

API reference: https://www.volcengine.com/docs/82379/1824136

Supports:
- Text-to-image
- Image-to-image (single or multiple reference images)
- Group image generation (sequential_image_generation)
- Streaming output (not yet implemented)
"""

import base64
import os
import time
import uuid

import requests

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class DoubaoImageProvider(AbstractBaseProvider):
    """
    Adapter for Doubao-Seedream-4.5 via Volcengine ARK.

    Calls ``POST /images/generations``.  Supports text-to-image and
    image-to-image with up to 14 reference images.

    Optional knobs via *request.options*:
    - ``size``: ``"2K"`` (default), ``"4K"``, or ``"WxH"`` pixel value.
    - ``response_format``: ``"url"`` (default) or ``"b64_json"``.
    - ``watermark``: bool, default ``False``.
    - ``sequential_image_generation``: ``"auto"`` or ``"disabled"`` (default).
    - ``max_images``: int, max images for group mode (1-15, default 15).
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

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """
        Generate an image from a text prompt, optionally conditioned on
        one or more reference images.

        Args:
            request:
                - prompt: Text description of the desired image.
                - reference_images: Optional list of image bytes for
                  style / subject conditioning.
                - options:
                    - size: "2K" (default), "4K", or "WxH" pixels.
                    - response_format: "url" or "b64_json".
                    - watermark: bool (default False).
                    - sequential_image_generation: "auto" or "disabled".
                    - max_images: int (1-15).

        Returns:
            GatewayResponse with ``content`` as image bytes.
        """
        request_id = getattr(request, "_request_id", None) or str(uuid.uuid4())
        start = time.time()

        # ---- Build payload ---------------------------------------------------
        size = request.options.get("size", "2K")
        response_format = request.options.get("response_format", "url")
        watermark = request.options.get("watermark", False)

        payload: dict = {
            "model": self.model,
            "prompt": request.prompt,
            "size": size,
            "response_format": response_format,
            "watermark": watermark,
        }

        # Reference images (style consistency & subject reference)
        if request.reference_images:
            image_list = []
            for img_bytes in request.reference_images:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                image_list.append(f"data:image/png;base64,{b64}")
            # Single image → string; multiple → array
            payload["image"] = image_list if len(image_list) > 1 else image_list[0]

        # Group image generation (sequential_image_generation)
        sequential_mode = request.options.get("sequential_image_generation", "disabled")
        payload["sequential_image_generation"] = sequential_mode
        if sequential_mode == "auto":
            max_images = request.options.get("max_images", 15)
            payload["sequential_image_generation_options"] = {
                "max_images": max_images,
            }

        # ---- Call ARK images API ---------------------------------------------
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        url = f"{self._endpoint}/images/generations"

        try:
            http_resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"Doubao image generation request failed: {exc}"
            ) from exc

        if http_resp.status_code != 200:
            raise RuntimeError(
                f"Doubao image generation error (status={http_resp.status_code}): "
                f"{http_resp.text[:500]}"
            )

        data = http_resp.json()
        latency = (time.time() - start) * 1000

        # ---- Check for top-level error ---------------------------------------
        if "error" in data and not data.get("data"):
            err = data["error"]
            raise RuntimeError(
                f"Doubao image generation failed: "
                f"code={err.get('code', 'unknown')}, "
                f"message={err.get('message', 'unknown')}"
            )

        # ---- Extract image(s) ------------------------------------------------
        image_data_list = data.get("data", [])
        if not image_data_list:
            raise RuntimeError(
                f"Doubao image generation returned no data: "
                f"{str(data)[:300]}"
            )

        # Collect all generated images
        images: list[bytes] = []
        resolutions: list[str] = []
        errors: list[str] = []

        for item in image_data_list:
            # Per-image error
            if "error" in item:
                err = item["error"]
                errors.append(
                    f"code={err.get('code', 'unknown')}: "
                    f"{err.get('message', '')}"
                )
                continue

            # Extract image bytes
            if response_format == "b64_json":
                b64_data = item.get("b64_json", "")
                if b64_data:
                    images.append(base64.b64decode(b64_data))
            else:
                img_url = item.get("url", "")
                if img_url:
                    try:
                        img_resp = requests.get(img_url, timeout=60)
                        img_resp.raise_for_status()
                        images.append(img_resp.content)
                    except requests.exceptions.RequestException as exc:
                        errors.append(f"Failed to download from {img_url}: {exc}")

            # Record resolution
            res = item.get("size", "")
            if res:
                resolutions.append(res)

        # If all images failed, raise
        if not images:
            raise RuntimeError(
                f"Doubao image generation: all images failed. "
                f"Errors: {'; '.join(errors) if errors else 'unknown'}"
            )

        # Log per-image failures as warnings (non-fatal in group mode)
        if errors:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                "Doubao image generation: %d/%d images failed: %s",
                len(errors), len(image_data_list), "; ".join(errors),
            )

        # ---- Build usage ---------------------------------------------------
        usage_data = data.get("usage", {})
        usage = UsageStats()
        usage.images = usage_data.get("generated_images", len(images))
        usage.output_tokens = usage_data.get("output_tokens", 0)
        if resolutions:
            usage.resolution = resolutions[0]
        usage.cost = self.calculate_cost(usage)

        # Return first image as primary content (group mode: caller can iterate)
        return GatewayResponse(
            request_id=request_id,
            task=request.task,
            provider=self.name,
            model=self.model,
            content=images[0],
            usage=usage,
            latency_ms=latency,
            raw_response=data,
        )

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        """
        Estimate cost based on output tokens.

        Pricing formula (official):
            output_tokens = sum(image_width * image_height) / 256

        Cost placeholder — update once official CNY/token pricing is confirmed.
        Current estimate: 0.001 CNY per 1K output tokens.
        """
        return round((usage.output_tokens / 1000.0) * 0.001, 6)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable_markers = [
            "429", "rate limit",
            "500", "502", "503", "504",
            "timeout", "connection", "reset by peer",
            "service unavailable",
            "throttling",
        ]
        return any(marker in error_str for marker in retryable_markers)
