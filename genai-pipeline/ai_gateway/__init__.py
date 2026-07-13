"""
AI Gateway — unified interface for LLM, Image, TTS, and Video generation.

Primary API::

    from ai_gateway import generate

    resp = generate(task="story", prompt="...", options={"response_format": "json"})
    sr   = generate(task="search", prompt="最新科技新闻")
    img  = generate(task="image", prompt="...")
    tts  = generate(task="voice", prompt="...")
    vid  = generate(task="video", prompt="...", options={"duration": 8})
"""

from .gateway import Gateway, generate
from .models import GatewayRequest, GatewayResponse, UsageStats

__all__ = [
    "Gateway",
    "GatewayRequest",
    "GatewayResponse",
    "UsageStats",
    "generate",
]
