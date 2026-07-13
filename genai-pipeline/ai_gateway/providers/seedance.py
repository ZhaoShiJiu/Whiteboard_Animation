"""
Doubao-Seedance-2.0 — video generation provider via Volcengine Ark API.
"""

import os
import time
import uuid

import requests

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class SeedanceProvider(AbstractBaseProvider):
    """
    Adapter for Doubao-Seedance-2.0 via Volcengine Ark.

    The Ark API uses an OpenAI-compatible scheme for video generation:
    1. Submit a video generation task (image + prompt) → get task_id
    2. Poll until completed
    3. Download the generated video
    """

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self._api_key = os.getenv(config.get("api_key_env", ""))
        if not self._api_key:
            raise ValueError(
                f"Ark API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'ARK_API_KEY')}'."
            )
        self._endpoint = config.get(
            "endpoint", "https://ark.cn-beijing.volces.com/api/v3"
        )
        self._poll_interval = config.get("poll_interval", 15)

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        request_id = str(uuid.uuid4())
        start = time.time()

        duration = request.options.get("duration", 8)
        resolution = request.options.get("resolution", "720p")
        aspect_ratio = request.options.get("aspect_ratio", "16:9")

        # ---- Build prompt --------------------------------------------------
        enhanced_prompt = (
            f"Whiteboard animation video showing: {request.prompt}. "
            "CRITICAL: The generated video MUST be a clean whiteboard animation "
            "video with a clean white background, hand-drawn line drawings/sketches, "
            "matching the style and content of the first frame. "
            "DO NOT add any realistic hands, markers, or pens drawing on the screen. "
            "Draw only the artwork."
        )

        # ---- Build headers ------------------------------------------------
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        # ---- Submit video generation task ----------------------------------
        submit_payload = {
            "model": self.model,
            "prompt": enhanced_prompt,
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        }

        # Include first-frame image if provided
        if request.reference_images and len(request.reference_images) > 0:
            import base64
            img_b64 = base64.b64encode(request.reference_images[0]).decode("utf-8")
            submit_payload["image"] = img_b64

        submit_url = f"{self._endpoint}/video/generations"

        submit_resp = requests.post(
            submit_url,
            headers=headers,
            json=submit_payload,
            timeout=60,
        )

        if submit_resp.status_code != 200:
            raise RuntimeError(
                f"Seedance submit error (status={submit_resp.status_code}): "
                f"{submit_resp.text}"
            )

        submit_data = submit_resp.json()
        task_id = submit_data.get("id") or submit_data.get("task_id")
        if not task_id:
            raise RuntimeError(
                f"Seedance response missing task id: {submit_data}"
            )

        # ---- Poll for completion -------------------------------------------
        video_url = self._poll_task(task_id, headers)

        # ---- Download video ------------------------------------------------
        video_bytes = self._download_video(video_url)

        latency = (time.time() - start) * 1000

        # ---- Build usage ---------------------------------------------------
        usage = UsageStats()
        usage.duration = float(duration)
        usage.resolution = resolution
        usage.cost = self.calculate_cost(usage)

        return GatewayResponse(
            request_id=request_id,
            task=request.task,
            provider=self.name,
            model=self.model,
            content=video_bytes,
            usage=usage,
            latency_ms=latency,
            raw_response={"task_id": task_id, "video_url": video_url},
        )

    # ------------------------------------------------------------------
    def _poll_task(self, task_id: str, headers: dict) -> str:
        """Poll the video generation task until completion. Returns video URL."""
        poll_url = f"{self._endpoint}/video/generations/{task_id}"

        max_wait = self.timeout
        elapsed = 0.0

        while elapsed < max_wait:
            time.sleep(self._poll_interval)
            elapsed += self._poll_interval

            resp = requests.get(poll_url, headers=headers, timeout=30)

            if resp.status_code != 200:
                # If transient, keep polling
                if resp.status_code >= 500:
                    continue
                raise RuntimeError(
                    f"Seedance poll error (status={resp.status_code}): {resp.text}"
                )

            data = resp.json()
            status = data.get("status", "").lower()

            if status in ("completed", "succeeded", "done"):
                video_url = (
                    data.get("video_url")
                    or data.get("output", {}).get("video_url")
                    or ""
                )
                if not video_url:
                    # Video bytes may be inline
                    video_b64 = (
                        data.get("video")
                        or data.get("output", {}).get("video")
                    )
                    if video_b64:
                        return f"data:inline:{video_b64}"
                    raise RuntimeError("Seedance task completed but no video URL found.")
                return video_url

            if status in ("failed", "cancelled", "error"):
                error_info = data.get("error", data.get("message", "unknown"))
                raise RuntimeError(f"Seedance task {task_id} failed: {error_info}")

            # Still processing — continue polling

        raise TimeoutError(
            f"Seedance task {task_id} did not complete within {max_wait}s."
        )

    # ------------------------------------------------------------------
    def _download_video(self, video_url: str) -> bytes:
        """Download the generated video, handling inline base64 as well."""
        if video_url.startswith("data:inline:"):
            import base64
            return base64.b64decode(video_url.split(":", 2)[2])

        resp = requests.get(video_url, timeout=120)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        per_second = 0.15 if usage.resolution == "1080p" else 0.10
        return round(usage.duration * per_second, 4)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable = [
            "timeout", "connection", "500", "502", "503", "504",
            "poll", "transient",
        ]
        return any(m in error_str for m in retryable)
