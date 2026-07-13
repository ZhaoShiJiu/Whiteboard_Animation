"""
Unified data structures for the AI Gateway.

All tool callers use GatewayRequest / GatewayResponse — they never need to
know which provider is serving the request.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UsageStats:
    """Unified usage & cost model — every provider populates the fields relevant to its type."""

    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    characters: int = 0       # TTS input character count
    duration: float = 0.0     # video / audio duration in seconds
    resolution: str = ""      # e.g. "2048x2048", "1080p"
    cost: float = 0.0


@dataclass
class GatewayRequest:
    """A single request to the AI Gateway.

    Attributes:
        task: The logical task name — "story", "image", "voice", "video".
        prompt: The primary text prompt.
        reference_images: Optional image bytes for image/video providers that
                          need a reference frame (e.g. style consistency, first frame).
        options: Provider-specific knobs (language, aspect_ratio, duration,
                 response_format, voice_id, …).
    """

    task: str
    prompt: str
    reference_images: Optional[List[bytes]] = None
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayResponse:
    """A unified response from the AI Gateway.

    Attributes:
        request_id: UUID for tracing.
        task: Echo of the request task.
        provider: Which provider served this request (e.g. "deepseek").
        model: The concrete model string (e.g. "deepseek-v4-pro").
        content: The primary payload — str for LLM, bytes for image/audio/video,
                 or a file-path string.
        usage: Token / image / character / duration stats + cost.
        latency_ms: End-to-end latency in milliseconds.
        raw_response: The original provider response for debugging (optional).
    """

    request_id: str
    task: str
    provider: str
    model: str
    content: Any
    usage: UsageStats = field(default_factory=UsageStats)
    latency_ms: float = 0.0
    raw_response: Any = None
    subtitles: Optional[list] = None  # TTS providers: word/sentence-level timestamps from native subtitle APIs
