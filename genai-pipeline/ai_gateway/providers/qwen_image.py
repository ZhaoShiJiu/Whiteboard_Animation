"""
Qwen-Image-2.0-Pro — image generation provider via Alibaba DashScope.
"""

import base64
import os
import time
import uuid

import dashscope
from dashscope import MultiModalConversation

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class QwenImageProvider(AbstractBaseProvider):
    """Adapter for Qwen-Image-2.0-Pro via DashScope multimodal generation."""

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        api_key = os.getenv(config.get("api_key_env", ""))
        if api_key:
            dashscope.api_key = api_key
        else:
            raise ValueError(
                f"DashScope API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'DASHSCOPE_API_KEY')}'."
            )

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        request_id = getattr(request, "_request_id", None) or str(uuid.uuid4())
        start = time.time()

        # ---- Build messages ------------------------------------------------
        # All content (images + text) MUST be in a SINGLE user message.
        # DashScope image editing API requires exactly one message object in the array.
        content_parts = []

        # Reference images (style consistency & subject reference)
        if request.reference_images:
            for img_bytes in request.reference_images:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                content_parts.append({"image": f"data:image/png;base64,{b64}"})

        # Main prompt
        prompt_text = request.prompt
        aspect_ratio = request.options.get("aspect_ratio", "16:9")
        prompt_text += f" Ensure the generated image is in {aspect_ratio} aspect ratio."
        content_parts.append({"text": prompt_text})

        messages = [{"role": "user", "content": content_parts}]

        # ---- Call DashScope API -------------------------------------------
        raw = MultiModalConversation.call(
            model=self.model,
            messages=messages,
        )

        latency = (time.time() - start) * 1000

        # ---- Handle errors ------------------------------------------------
        if raw.status_code != 200:
            error_msg = f"DashScope API error (code={raw.status_code}): {raw.message}"
            raise RuntimeError(error_msg)

        # ---- Extract image -------------------------------------------------
        content = None
        resolution = ""
        images_count = 0

        if raw.output and raw.output.choices:
            for choice in raw.output.choices:
                msg = choice.message
                if msg.content:
                    for part in msg.content:
                        # Compatible with both dict and object parts
                        img_url = None
                        if isinstance(part, dict):
                            img_url = part.get("image")
                        elif hasattr(part, "image"):
                            img_url = part.image
                        if img_url:
                            images_count += 1
                            # Download image bytes from URL
                            import requests as http_requests
                            img_resp = http_requests.get(img_url, timeout=30)
                            img_resp.raise_for_status()
                            content = img_resp.content
                            # Try to detect resolution from Pillow
                            try:
                                from io import BytesIO
                                from PIL import Image
                                im = Image.open(BytesIO(content))
                                resolution = f"{im.width}x{im.height}"
                            except Exception:
                                resolution = request.options.get("resolution", "2048x2048")

        if content is None:
            raise RuntimeError(
                f"DashScope returned no image. Message: {getattr(raw, 'message', 'unknown')}"
            )

        # ---- Build usage ---------------------------------------------------
        usage = UsageStats()
        usage.images = max(images_count, 1)
        usage.resolution = resolution
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
        per_image = 0.04
        if "2048" in usage.resolution:
            per_image = 0.10
        return round(usage.images * per_image, 4)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable = [
            "throttling", "internalerror", "serviceunavailable",
            "timeout", "connection", "rate",
        ]
        return any(m in error_str for m in retryable)
