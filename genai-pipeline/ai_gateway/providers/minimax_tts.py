"""
MiniMax Speech-2.8-HD — TTS provider.
"""

import base64
import json
import os
import time
import uuid

import requests

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider


class MiniMaxTTSProvider(AbstractBaseProvider):
    """Adapter for MiniMax Speech-2.8-HD TTS v2 API."""

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self._api_key = os.getenv(config.get("api_key_env", ""))
        if not self._api_key:
            raise ValueError(
                f"MiniMax API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'MINIMAX_API_KEY')}'."
            )
        self._endpoint = config.get("endpoint", "https://api.minimax.chat/v1/t2a_v2")

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        request_id = getattr(request, "_request_id", None) or str(uuid.uuid4())
        start = time.time()

        # ---- Build payload -------------------------------------------------
        voice_id = request.options.get("voice_id", "Chinese (Mandarin)_Stubborn_Friend")
        speed = float(request.options.get("speed", 1.0))
        vol = float(request.options.get("volume", 1.0))
        pitch = int(request.options.get("pitch", 0))
        emotion = request.options.get("emotion")
        sample_rate = int(request.options.get("sample_rate", 32000))
        audio_format = request.options.get("format", "mp3")

        subtitle_enable = request.options.get("subtitle_enable", True)
        subtitle_type = request.options.get("subtitle_type", "sentence")
        language_boost = request.options.get("language_boost")

        voice_setting = {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        }
        if emotion:
            voice_setting["emotion"] = emotion

        payload = {
            "model": self.model,
            "text": request.prompt,
            "stream": False,
            "subtitle_enable": subtitle_enable,
            "subtitle_type": subtitle_type,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": sample_rate,
                "format": audio_format,
            },
        }
        if language_boost:
            payload["language_boost"] = language_boost

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # ---- Call API ------------------------------------------------------
        resp = requests.post(
            self._endpoint,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )

        latency = (time.time() - start) * 1000

        # ---- Handle errors ------------------------------------------------
        if resp.status_code != 200:
            error_msg = f"MiniMax API error (status={resp.status_code}): {resp.text}"
            raise RuntimeError(error_msg)

        data = resp.json()
        base_resp = data.get("base_resp", {})
        status_code = base_resp.get("status_code", -1)
        if status_code != 0:
            error_msg = (
                f"MiniMax API returned status_code={status_code}: "
                f"{base_resp.get('status_msg', 'unknown error')}"
            )
            raise RuntimeError(error_msg)

        # ---- Extract audio -------------------------------------------------
        audio_hex = data.get("data", {}).get("audio")
        if not audio_hex:
            # Try alternate field
            audio_hex = data.get("audio")

        if not audio_hex:
            raise RuntimeError("MiniMax returned no audio data.")

        content = bytes.fromhex(audio_hex)

        # ---- Extract subtitle timestamps ----------------------------------
        # MiniMax returns a subtitle_file URL in data.data.
        # The JSON is an array; actual field names are time_begin/time_end (ms, float).
        # Official docs claim start_time/end_time — we probe both for safety.
        subtitles = None
        subtitle_file_url = (
            data.get("data", {}).get("subtitle_file")
            or data.get("subtitle_file")
        )
        if subtitle_file_url:
            try:
                sub_resp = requests.get(subtitle_file_url, timeout=30)
                if sub_resp.status_code == 200:
                    sub_data = sub_resp.json()
                    raw_list = sub_data if isinstance(sub_data, list) else []
                    if raw_list:
                        subtitles = [
                            {
                                "start": float(s.get("time_begin", s.get("start_time", 0))) / 1000.0,
                                "end": float(s.get("time_end", s.get("end_time", 0))) / 1000.0,
                                "text": str(s.get("text", "")),
                            }
                            for s in raw_list
                        ]
            except Exception:
                # Subtitle download is best-effort; never fail the TTS call over it
                pass

        # ---- Calculate characters & duration -------------------------------
        char_count = len(request.prompt)
        # Estimate duration: roughly 4 chars/second for Chinese, 12 chars/sec for English
        text_sample = request.prompt[:100]
        has_cjk = any("一" <= c <= "鿿" for c in text_sample)
        char_per_sec = 4.0 if has_cjk else 12.0
        estimated_duration = char_count / char_per_sec

        # ---- Build usage ---------------------------------------------------
        usage = UsageStats()
        usage.characters = char_count
        usage.duration = estimated_duration
        usage.cost = self.calculate_cost(usage)

        return GatewayResponse(
            request_id=request_id,
            task=request.task,
            provider=self.name,
            model=self.model,
            content=content,
            usage=usage,
            latency_ms=latency,
            raw_response=data,
            subtitles=subtitles,
        )

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        return round((usage.characters / 1000.0) * 0.015, 6)

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable = [
            "429", "1001", "1002", "1020",
            "timeout", "connection",
        ]
        return any(m in error_str for m in retryable)
