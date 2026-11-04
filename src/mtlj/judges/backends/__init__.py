"""Inference backends used by judge implementations."""

from mtlj.judges.backends.base import InferenceBackend
from mtlj.judges.backends.ollama import OllamaBackend

__all__ = ["InferenceBackend", "OllamaBackend"]
