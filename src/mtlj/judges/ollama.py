from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from mtlj.judges.base import Context, Judge


class OllamaError(RuntimeError):
    """Raised when Ollama cannot return a valid judge score."""


class OllamaJudge(Judge):
    """Judge implementation that asks a local Ollama model for a score.

    The model must return a JSON object such as {"score": 0.82}.
    The score is expected to be in the configured score_range (default [0, 1]).
    """

    name = "ollama"
    score_range = (0.0, 1.0)
    higher_is_better = True

    def __init__(
        self,
        model: str = "llama3.2:3b",
        *,
        base_url: str | None = None,
        timeout: float = 120.0,
        version: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.version = version or model
        self.base_url = (
            base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self.timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self.quantization: str | None = None

    def score_raw(self, *, input: str, output: str, context: Context) -> float:
        prompt = self._build_prompt(input=input, output=output, context=context)
        try:
            response = self._client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an evaluation judge. Evaluate the output "
                                "against the input and context. Return only a JSON "
                                'object with one numeric key named "score", from '
                                "0.0 (poor) to 1.0 (excellent)."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["message"]["content"]
            parsed = json.loads(content)
            score = parsed["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError("score must be numeric")
            return float(score)
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise OllamaError(f"Ollama judge request failed: {exc}") from exc

    def refresh_metadata(self) -> dict[str, str | None]:
        """Fetch model details, including quantization, from Ollama's /api/show."""
        try:
            response = self._client.post(
                f"{self.base_url}/api/show",
                json={"model": self.model},
            )
            response.raise_for_status()
            details = response.json().get("details", {})
            self.quantization = details.get("quantization_level")
            return {
                "backend": "ollama",
                "model": self.model,
                "version": self.version,
                "quantization": self.quantization,
            }
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaError(f"Could not read Ollama model metadata: {exc}") from exc

    @staticmethod
    def _build_prompt(*, input: str, output: str, context: Context) -> str:
        return (
            "Evaluate the candidate output.\n\n"
            f"Input:\n{input}\n\n"
            f"Candidate output:\n{output}\n\n"
            "Context (JSON):\n"
            f"{json.dumps(dict(context), ensure_ascii=False, default=str)}\n\n"
            'Return JSON only: {"score": 0.0}'
        )

    def close(self) -> None:
        """Close the HTTP client if this instance created it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OllamaJudge:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
