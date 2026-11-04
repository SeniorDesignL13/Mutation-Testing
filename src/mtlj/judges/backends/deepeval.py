"""DeepEval model bridge for interchangeable inference backends."""

from __future__ import annotations

import asyncio
from typing import Any

from mtlj.judges.backends.base import InferenceBackend


class BackendDeepEvalModel:
    """Adapt an inference backend to DeepEval's custom-model interface.

    DeepEval remains responsible for evaluating faithfulness,
    claim decomposition, and scoring.
    """

    def __init__(
        self,
        backend: InferenceBackend,
        *,
        system_prompt: str | None = None,
    ) -> None:
        self.backend = backend
        self.system_prompt = system_prompt

    def load_model(self) -> InferenceBackend:
        return self.backend

    def get_model_name(self) -> str:
        return self.backend.metadata.model

    def generate(
        self,
        prompt: str,
        schema: Any = None,
    ) -> Any:
        result = self.backend.generate(
            prompt,
            system_prompt=self.system_prompt,
        )

        if schema is not None:
            return schema.model_validate_json(result)

        return result

    async def a_generate(
        self,
        prompt: str,
        schema: Any = None,
    ) -> Any:
        result = await asyncio.to_thread(
            self.generate,
            prompt,
            schema,
        )
        return result
