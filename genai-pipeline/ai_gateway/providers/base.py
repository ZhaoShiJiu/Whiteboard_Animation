"""
Abstract base class for all AI Gateway providers.

Every provider implements: generate(), calculate_cost(), and is_retryable().
"""

from abc import ABC, abstractmethod

from ..models import GatewayRequest, GatewayResponse, UsageStats


class AbstractBaseProvider(ABC):
    """Contract every provider adapter must fulfil."""

    def __init__(self, name: str, config: dict):
        """
        Args:
            name: Provider key from gateway.yaml (e.g. "deepseek").
            config: The full provider section from gateway.yaml.
        """
        self.name = name
        self.config = config
        self.model: str = config.get("model", "")
        self.type: str = config.get("type", "")
        self.timeout: int = config.get("timeout", 60)

    @abstractmethod
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """Send a request to the provider's API and return a unified response."""

    @abstractmethod
    def calculate_cost(self, usage: UsageStats) -> float:
        """Calculate the cost for this call based on provider pricing."""

    @abstractmethod
    def is_retryable(self, error: Exception) -> bool:
        """Return True if this error is a transient failure worth retrying."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} model={self.model}>"
