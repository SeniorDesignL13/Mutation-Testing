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
import json
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
# Ragas's own temperature (``InstructorModelArgs``), so the judge runs as Ragas ships it. A test
# checks it still matches the installed Ragas.
DEFAULT_TEMPERATURE = 0.01
# Ragas's default is 1024. On the gold set the two LLM calls of the longest answer (45 statements)
# used about 2000 tokens between them, so 1024 would risk cutting off the verdicts (docs/judges.md).
DEFAULT_MAX_TOKENS = 4096

Measure = Callable[[str, str, list[str]], float | RawScore]
# Called after each call with the tokens its LLM calls used: (prompt tokens, completion tokens).
OnUsage = Callable[[int, int], None]


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


def _counting(llm: Any, on_usage: OnUsage) -> Any:
    """``llm`` that adds up the tokens of every call and reports them to ``on_usage`` at the end.

    The counts are estimates: Ragas's structured-output calls don't return OpenAI's usage, so the
    prompt, the response schema and the answer are counted with tiktoken's o200k encoding.
    """
    import tiktoken
    from ragas.llms.base import InstructorBaseRagasLLM

    encoding = tiktoken.get_encoding("o200k_base")

    class CountingLLM(InstructorBaseRagasLLM):
        prompt_tokens = 0
        completion_tokens = 0

        def generate(self, prompt: str, response_model: Any) -> Any:
            raise NotImplementedError("the metric runs async")

        async def agenerate(self, prompt: str, response_model: Any) -> Any:
            schema = json.dumps(response_model.model_json_schema())
            self.prompt_tokens += len(encoding.encode(prompt + schema))
            try:
                result = await llm.agenerate(prompt, response_model)
            except BaseException:
                # The prompt was billed even though no answer came back.
                on_usage(self.prompt_tokens, self.completion_tokens)
                raise
            self.completion_tokens += len(encoding.encode(result.model_dump_json()))
            return result

    return CountingLLM()


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
    on_usage: OnUsage | None = None,
) -> RawScore:
    """Score ``output`` against ``passages`` with one fresh Ragas ``Faithfulness`` metric.

    ``llm`` replaces the OpenAI-backed Ragas LLM, for tests. ``on_usage``, if given, is called
    once per call with the estimated prompt and completion tokens it used (see ``_counting``),
    also when the call fails part way, since the LLM calls already made were billed. The fixture
    recorder uses it to enforce a spending cap.

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
        counter = _counting(judge_llm, on_usage) if on_usage is not None else None
        try:
            metric = metric_class(llm=counter or judge_llm)
            result = await metric.ascore(
                user_input=input, response=output, retrieved_contexts=passages
            )
        finally:
            if client is not None:
                await client.close()
        if counter is not None:
            on_usage(counter.prompt_tokens, counter.completion_tokens)
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
