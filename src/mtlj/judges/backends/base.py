"""Backend-independent interface for model inference."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackendMetadata:
    provider: str
    model: str
    quantization: str | None = None
    version: str | None = None


class InferenceBackend(ABC):
    """Interface implemented by local and hosted inference providers."""

    @property
    @abstractmethod
    def metadata(self) -> BackendMetadata:
        """Return the provider and model configuration."""
        ...

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Generate a response from the underlying model."""
        ...
