"""Ollama inference backend.

Ollama is an inference provider, not a judge. Any judge implementation
can use this backend to serve its model locally.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from mtlj.judges.backends.base import BackendMetadata, InferenceBackend


@dataclass(slots=True)
class OllamaBackend(InferenceBackend):
    model: str
    quantization: str | None = None
    base_url: str = "http://localhost:11434"
    timeout: float = 300.0
    temperature: float = 0.0

    @property
    def metadata(self) -> BackendMetadata:
        return BackendMetadata(
            provider="ollama",
            model=self.model,
            quantization=self.quantization,
        )

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
    ) -> str:
        messages = []

        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        messages.append(
            {
                "role": "user",
                "content": prompt,
            }
        )

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": (self.temperature if temperature is None else temperature),
            },
        }

        with httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout,
        ) as client:
            response = client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Ollama returned an invalid chat response") from exc
