"""
HappyHorse-1.0/1.1-I2V — image-to-video provider via Alibaba DashScope API.

Takes a reference image (first frame) and a text prompt, generates a dynamic video.
Supports both happyhorse-1.0-i2v and happyhorse-1.1-i2v models.

API flow (async — mandatory, synchronous calls are NOT supported):
  1. POST /api/v1/services/aigc/video-generation/video-synthesis  →  task_id
     (requires X-DashScope-Async: enable header)
  2. GET  /api/v1/tasks/{task_id}  (poll until SUCCEEDED / FAILED)
  3. Download video from the returned video_url (valid for 24 h)
"""

import base64
import os
import time
import uuid

import requests

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class HappyHorseProvider(AbstractBaseProvider):
    """
    Adapter for HappyHorse I2V via Alibaba DashScope.

    Reuses DASHSCOPE_API_KEY (same account as Qwen Image).
    """

    # DashScope async task statuses (all UPPERCASE per official docs)
    _STATUS_PENDING = "PENDING"
    _STATUS_RUNNING = "RUNNING"
    _STATUS_SUCCEEDED = "SUCCEEDED"
    _STATUS_FAILED = "FAILED"
    _STATUS_CANCELED = "CANCELED"
    _STATUS_UNKNOWN = "UNKNOWN"

    _TERMINAL_STATUSES = {_STATUS_SUCCEEDED, _STATUS_FAILED, _STATUS_CANCELED, _STATUS_UNKNOWN}

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self._api_key = os.getenv(config.get("api_key_env", ""))
        if not self._api_key:
            raise ValueError(
                f"DashScope API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'DASHSCOPE_API_KEY')}'."
            )
        # Base endpoint for DashScope (same base used for submit and poll paths).
        # Recommend switching to workspace-specific domain for better perf:
        #   https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1
        self._endpoint = config.get(
            "endpoint",
            "https://dashscope.aliyuncs.com/api/v1",
        )
        self._poll_interval = config.get("poll_interval", 15)

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        request_id = getattr(request, "_request_id", None) or str(uuid.uuid4())
        start = time.time()

        duration = request.options.get("duration", 8)
        # DashScope uses uppercase: "720P" / "1080P" — normalize caller input
        resolution = request.options.get("resolution", "720P").upper()

        # ---- Build prompt --------------------------------------------------
        enhanced_prompt = (
            f"Whiteboard animation video showing: {request.prompt}. "
            "CRITICAL: The generated video MUST be a clean whiteboard animation "
            "video with a clean white background, hand-drawn line drawings/sketches, "
            "matching the style and content of the first frame. "
            "DO NOT add any realistic hands, markers, or pens drawing on the screen. "
            "Draw only the artwork."
        )

        # ---- Build headers (X-DashScope-Async is REQUIRED) ------------------
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-DashScope-Async": "enable",
        }

        # ---- Build input media (first-frame image) --------------------------
        input_block: dict = {"prompt": enhanced_prompt}

        if request.reference_images and len(request.reference_images) > 0:
            img_b64 = base64.b64encode(request.reference_images[0]).decode("utf-8")
            input_block["media"] = [
                {
                    "type": "first_frame",
                    "url": f"data:image/png;base64,{img_b64}",
                }
            ]

        # ---- Submit video generation task ----------------------------------
        submit_payload = {
            "model": self.model,
            "input": input_block,
            "parameters": {
                "duration": duration,
                "resolution": resolution,
                "watermark": False,
            },
        }

        submit_url = f"{self._endpoint}/services/aigc/video-generation/video-synthesis"

        submit_resp = requests.post(
            submit_url,
            headers=headers,
            json=submit_payload,
            timeout=60,
        )

        if submit_resp.status_code != 200:
            raise RuntimeError(
                f"HappyHorse submit error (status={submit_resp.status_code}): "
                f"{submit_resp.text}"
            )

        submit_data = submit_resp.json()
        task_id = (
            submit_data.get("output", {}).get("task_id")
            or submit_data.get("task_id")
            or submit_data.get("id")
        )
        if not task_id:
            raise RuntimeError(
                f"HappyHorse response missing task_id: {submit_data}"
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
        poll_url = f"{self._endpoint}/tasks/{task_id}"

        max_wait = self.timeout
        elapsed = 0.0

        while elapsed < max_wait:
            time.sleep(self._poll_interval)
            elapsed += self._poll_interval

            resp = requests.get(poll_url, headers=headers, timeout=30)

            if resp.status_code != 200:
                if resp.status_code >= 500:
                    continue
                raise RuntimeError(
                    f"HappyHorse poll error (status={resp.status_code}): {resp.text}"
                )

            data = resp.json()
            output = data.get("output", {})
            task_status = output.get("task_status", "").upper()

            if task_status == self._STATUS_SUCCEEDED:
                video_url = output.get("video_url", "")
                if not video_url:
                    raise RuntimeError(
                        "HappyHorse task SUCCEEDED but no video_url found."
                    )
                return video_url

            if task_status in (self._STATUS_FAILED, self._STATUS_CANCELED):
                error_info = output.get("message") or data.get("message", "unknown")
                raise RuntimeError(
                    f"HappyHorse task {task_id} {task_status}: {error_info}"
                )

            if task_status == self._STATUS_UNKNOWN:
                raise RuntimeError(
                    f"HappyHorse task {task_id} UNKNOWN: task may have expired "
                    f"(>24 h) or never existed."
                )

            # PENDING / RUNNING — continue polling

        raise TimeoutError(
            f"HappyHorse task {task_id} did not complete within {max_wait}s."
        )

    # ------------------------------------------------------------------
    def _download_video(self, video_url: str) -> bytes:
        """Download the generated video from URL (valid for 24 h)."""
        resp = requests.get(video_url, timeout=120)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        # HappyHorse pricing (adjust when official pricing is confirmed)
        # Placeholder: CNY 0.15/s at 1080P, 0.10/s at 720P
        per_second = 0.15 if usage.resolution and "1080" in usage.resolution.upper() else 0.10
        return round(usage.duration * per_second, 4)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable = [
            "timeout", "connection", "500", "502", "503", "504",
            "poll", "transient", "throttling",
        ]
        return any(m in error_str for m in retryable)
