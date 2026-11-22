"""Ragas's faithfulness metric as a judge.

Each call builds a fresh metric and a fresh OpenAI client, because the runner scores from several
threads at once and a call's statements and verdicts must not mix with another's. Ragas's metric
is async, so each call runs it in its own event loop, which is safe on the runner's worker
threads. The adapter reads ``OPENAI_API_KEY`` from the environment or a ``.env`` file in the
working directory.

This is the metric in ``ragas.metrics.collections`` (Ragas 0.4), not the legacy
``ragas.metrics.Faithfulness``, which wraps LangChain models. It makes two LLM calls: one splits
the answer into statements, one gives every statement a verdict of 1 (it can be inferred from the
context) or 0. The score is the share of verdicts that are 1. Unlike DeepEval, there is no
"borderline" verdict (see docs/judges.md), so the ``borderline`` count is always 0.

Ragas 0.4.3 can't be imported with the langchain-community in uv.lock, which dropped a module
Ragas still imports; ``_import_metric`` stands in for it until Ragas is fixed.
"""

from __future__ import annotations

import asyncio
import math
import sys
import types
from collections.abc import Callable
from functools import partial
from importlib.metadata import version as installed_version
from typing import TYPE_CHECKING, Any

from mtlj.judges.base import Context, Judge, RawScore, VerdictCounts

if TYPE_CHECKING:
    from ragas.metrics.collections import Faithfulness

DEFAULT_MODEL = "gpt-4o-mini"
# Ragas's own default is 0.01. Zero matches the DeepEval adapter, so the two differ by metric only.
DEFAULT_TEMPERATURE = 0.0
# Ragas's default of 1024 can cut off the verdicts for a long answer's many statements.
DEFAULT_MAX_TOKENS = 4096

Measure = Callable[[str, str, list[str]], float | RawScore]


def _import_metric() -> type[Faithfulness]:
    """Import Ragas's faithfulness metric.

    Imported here because Ragas adds seconds to startup, which every mtlj command would
    otherwise pay for.
    """
    module = "langchain_community.chat_models.vertexai"
    if module not in sys.modules:
        try:
            __import__(module)
        except ImportError:
            # ragas.llms only uses this class to tell LangChain models from others, and
            # langchain-community 0.4 no longer has the module.
            stub = types.ModuleType(module)
            stub.ChatVertexAI = type("ChatVertexAI", (), {})  # type: ignore[attr-defined]
            sys.modules[module] = stub
    from ragas.metrics.collections import Faithfulness

    return Faithfulness


def _recording(metric_class: type[Faithfulness]) -> type[Faithfulness]:
    """``metric_class`` with ``statements`` and ``verdicts`` attributes set as it runs.

    Ragas's result holds only the score, so the statements and their verdicts are taken from
    the two steps that produce them.
    """

    class RecordingFaithfulness(metric_class):  # type: ignore[valid-type, misc]
        statements: list[str] | None = None
        verdicts: list[Any] | None = None

        async def _create_statements(self, question: str, response: str) -> list[str]:
            self.statements = await super()._create_statements(question, response)
            return self.statements

        async def _create_verdicts(self, statements: list[str], context: str) -> Any:
            output = await super()._create_verdicts(statements, context)
            self.verdicts = list(output.statements)
            return output

    return RecordingFaithfulness


def _raw_score(score: float, statements: list[str], verdicts: list[Any]) -> RawScore:
    """The score with the verdict counts behind it, or without them when they don't add up."""
    if len(verdicts) > len(statements):
        # Ragas scores over the verdicts it got back, so counts above the claims would be invented.
        return RawScore(score)
    # Ragas calls a statement faithful when its verdict is truthy, so the counts do too.
    yes = sum(1 for verdict in verdicts if verdict.verdict)
    counts = VerdictCounts(yes, 0, len(verdicts) - yes)
    return RawScore(score, verdict_counts=counts, n_claims=len(statements))


def measure_faithfulness(
    input: str,
    output: str,
    passages: list[str],
    *,
    model: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    llm: Any = None,
) -> RawScore:
    """Score ``output`` against ``passages`` with one fresh Ragas ``Faithfulness`` metric.

    ``llm`` replaces the OpenAI-backed Ragas LLM, for tests.

    Raises:
        ValueError: if Ragas finds no statements in the answer (it scores that NaN), or the
            call lacks a question, an answer or passages.
    """
    metric_class = _recording(_import_metric())

    async def run() -> RawScore:
        client = None
        judge_llm = llm
        if judge_llm is None:
            from dotenv import load_dotenv
            from openai import AsyncOpenAI
            from ragas.llms import llm_factory

            load_dotenv()
            client = AsyncOpenAI()
            judge_llm = llm_factory(
                model, client=client, temperature=temperature, max_tokens=max_tokens
            )
        try:
            metric = metric_class(llm=judge_llm)
            result = await metric.ascore(
                user_input=input, response=output, retrieved_contexts=passages
            )
        finally:
            if client is not None:
                await client.close()
        score = float(result.value)
        if math.isnan(score):
            raise ValueError("Ragas found no statements in the answer, so it has no score")
        return _raw_score(score, metric.statements or [], metric.verdicts or [])

    return asyncio.run(run())


class RagasFaithfulnessJudge(Judge):
    """Scores how well an answer is supported by its passages, from 0 to 1.

    Args:
        model: the OpenAI model Ragas judges with.
        temperature: the sampling temperature of Ragas's LLM calls.
        max_tokens: the most tokens one Ragas LLM call may answer with.
        measure: replaces the Ragas call, for tests.
    """

    name = "ragas-faithfulness"
    score_range = (0.0, 1.0)
    higher_is_better = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        measure: Measure | None = None,
    ) -> None:
        self.model = model
        self.version = installed_version("ragas")
        self.temperature = temperature
        self.max_tokens = max_tokens
        if measure is None:
            # Load it now, so the seconds this takes don't land in the first scores' latency.
            _import_metric()
        self._measure = measure or partial(
            measure_faithfulness, model=model, temperature=temperature, max_tokens=max_tokens
        )

    def cache_settings(self) -> dict[str, Any]:
        """What besides name, model and version decides the score: the LLM's settings."""
        return {"temperature": self.temperature, "max_tokens": self.max_tokens}

    def score_raw(self, input: str, output: str, context: Context) -> float | RawScore:
        """Measure ``output``, passing the context's passages to Ragas in order."""
        passages = [passage["text"] for passage in context.get("passages", [])]
        return self._measure(input, output, passages)
