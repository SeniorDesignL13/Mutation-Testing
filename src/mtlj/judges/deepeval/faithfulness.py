"""DeepEval's faithfulness metric as a judge.

Each call builds a fresh metric, because a metric keeps its claims and verdicts on itself and
the runner scores from several threads at once. The metric runs synchronously, since the runner
already puts each call on its own worker thread.

DeepEval times out and retries its own calls to OpenAI; set
``DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE`` and ``DEEPEVAL_RETRY_MAX_ATTEMPTS`` to change
that. It reads ``OPENAI_API_KEY`` from the environment or a ``.env`` file in the working directory.

The two configs are different judges (a detection rate belongs to a judge *and* its config, see
docs/metrics.md), so they have different names. ``penalize_ambiguous_claims`` stops counting
"borderline" verdicts as faithful.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from functools import partial
from importlib import import_module
from importlib.metadata import version as installed_version
from typing import TYPE_CHECKING

from mtlj.judges.base import Context, Judge, RawScore, VerdictCounts

if TYPE_CHECKING:
    from deepeval.models import DeepEvalBaseLLM

DEFAULT_MODEL = "gpt-4o-mini"

Measure = Callable[[str, str, list[str]], float | RawScore]


def measure_faithfulness(
    input: str,
    output: str,
    passages: list[str],
    *,
    model: str | DeepEvalBaseLLM,
    penalize_ambiguous_claims: bool = False,
    on_cost: Callable[[float | None], None] | None = None,
) -> RawScore:
    """Score ``output`` against ``passages`` with one fresh ``FaithfulnessMetric``.

    ``on_cost``, if given, is called with what DeepEval says the call cost in US dollars (None
    when it can't tell), after the metric has run, even when the result is then rejected. The
    fixture recorder uses it to enforce a spending cap.

    Raises:
        ValueError: if the answer has claims but no verdict came back. DeepEval scores that
            1.0, though nothing was judged.
    """
    # Imported here because DeepEval adds seconds to startup, which every mtlj command would
    # otherwise pay for.
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.models import OpenAIModel
    from deepeval.test_case import LLMTestCase

    if isinstance(model, str):
        # A bare name would let DeepEval's USE_* provider settings choose the backend, while
        # every result still reported this model.
        model = OpenAIModel(model=model, temperature=0.0)
    metric = FaithfulnessMetric(
        model=model,
        eval_mode="llm",
        async_mode=False,
        include_reason=False,  # Doesn't affect the score, and would add a fourth LLM call.
        penalize_ambiguous_claims=penalize_ambiguous_claims,
    )
    case = LLMTestCase(input=input, actual_output=output, retrieval_context=passages)
    score = metric.measure(case, _show_indicator=False)
    if on_cost is not None:
        on_cost(metric.evaluation_cost)

    labels = Counter(str(verdict.verdict) for verdict in metric.verdicts)
    counts = VerdictCounts(labels["yes"], labels["borderline"], labels["no"])
    n_claims = len(metric.claims)
    if n_claims and not any(counts):
        raise ValueError(f"DeepEval returned no verdicts for {n_claims} claims")
    return RawScore(score, verdict_counts=counts, n_claims=n_claims)


class DeepEvalFaithfulnessJudge(Judge):
    """Scores how well an answer is supported by its passages, from 0 to 1.

    Args:
        model: the OpenAI model DeepEval judges with.
        penalize_ambiguous_claims: count "borderline" verdicts as unfaithful.
        measure: replaces the DeepEval call, for tests.
    """

    score_range = (0.0, 1.0)
    higher_is_better = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        penalize_ambiguous_claims: bool = False,
        measure: Measure | None = None,
    ) -> None:
        suffix = "-penalized" if penalize_ambiguous_claims else ""
        self.name = f"deepeval-faithfulness{suffix}"
        self.model = model
        self.version = installed_version("deepeval")
        self.penalize_ambiguous_claims = penalize_ambiguous_claims
        if measure is None:
            # Load it now, so the seconds this takes don't land in the first scores' latency.
            import_module("deepeval")
        self._measure = measure or partial(
            measure_faithfulness,
            model=model,
            penalize_ambiguous_claims=penalize_ambiguous_claims,
        )

    def score_raw(self, input: str, output: str, context: Context) -> float | RawScore:
        """Measure ``output``, passing the context's passages to DeepEval in order."""
        passages = [passage["text"] for passage in context.get("passages", [])]
        return self._measure(input, output, passages)
