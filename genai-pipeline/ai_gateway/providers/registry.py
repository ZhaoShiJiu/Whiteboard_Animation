"""
Provider registry — maps provider names to their adapter classes.
"""

from .base import AbstractBaseProvider
from .deepseek import DeepSeekProvider
from .doubao_search import DoubaoSearchProvider
from .happyhorse import HappyHorseProvider
from .minimax_tts import MiniMaxTTSProvider
from .qwen_image import QwenImageProvider
from .seedance import SeedanceProvider

#: Map provider type/name → adapter class
PROVIDER_CLASSES: dict[str, type[AbstractBaseProvider]] = {
    "deepseek": DeepSeekProvider,
    "doubao_search": DoubaoSearchProvider,
    "happyhorse": HappyHorseProvider,
    "qwen": QwenImageProvider,
    "minimax": MiniMaxTTSProvider,
    "seedance": SeedanceProvider,
}

#: Map provider type → usage type string for ai_usage table
PROVIDER_TYPE_MAP: dict[str, str] = {
    "deepseek": "llm",
    "doubao_search": "search",
    "happyhorse": "video",
    "qwen": "image",
    "minimax": "tts",
    "seedance": "video",
}


def create_provider(name: str, config: dict) -> AbstractBaseProvider:
    """Factory: instantiate a provider adapter by name."""
    cls = PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown provider: '{name}'. "
            f"Available: {list(PROVIDER_CLASSES.keys())}"
        )
    return cls(name, config)


__all__ = [
    "AbstractBaseProvider",
    "PROVIDER_CLASSES",
    "PROVIDER_TYPE_MAP",
    "create_provider",
]
