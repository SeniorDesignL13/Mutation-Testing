"""The DeepEval adapter, run through the real metric with a fake LLM (no network)."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from importlib.metadata import version

import pytest
from deepeval.models import DeepEvalBaseLLM

from mtlj.data.records import DatasetRecord, Passage
from mtlj.engine.runner import MemorySink, judge_context, run_judge
from mtlj.judges.base import VerdictCounts
from mtlj.judges.deepeval.faithfulness import (
    DEFAULT_MODEL,
    DeepEvalFaithfulnessJudge,
    measure_faithfulness,
)

PASSAGES = [
    {"passage_id": "p1", "text": "Paris is the capital of France.", "title": "Paris"},
    {"passage_id": "p2", "text": "France is in Western Europe.", "title": None},
    {"passage_id": "p3", "text": "The Seine flows through Paris.", "title": None},
]
CONTEXT = {"passages": PASSAGES}


class FakeLLM(DeepEvalBaseLLM):
    """Answers the metric's three prompts with canned claims and verdicts."""

    def __init__(self, verdicts: list[str], claims: list[str] | None = None) -> None:
        self.verdicts = verdicts
        self.claims = ["a claim"] * len(verdicts) if claims is None else claims
        self.prompts: dict[str, str] = {}
        super().__init__("fake-llm")

    def load_model(self) -> FakeLLM:
        return self

    def get_model_name(self) -> str:
        return "fake-llm"

    def generate(self, prompt: str, schema=None):
        kind = schema.__name__
        self.prompts[kind] = prompt
        if kind == "Truths":
            return schema(truths=["a truth"])
        if kind == "Claims":
            return schema(claims=self.claims)
        return schema(verdicts=[{"verdict": verdict} for verdict in self.verdicts])

    async def a_generate(self, prompt: str, schema=None):
        return self.generate(prompt, schema)


def judge_with(llm: FakeLLM, *, penalize: bool = False) -> DeepEvalFaithfulnessJudge:
    measure = partial(measure_faithfulness, model=llm, penalize_ambiguous_claims=penalize)
    return DeepEvalFaithfulnessJudge(penalize_ambiguous_claims=penalize, measure=measure)


def test_score_and_verdict_counts_come_from_the_metric():
    result = judge_with(FakeLLM(["yes", "yes", "borderline", "no"]))("q", "a", CONTEXT)

    assert result.score == pytest.approx(3 / 4)  # Borderline counts as faithful by default.
    assert result.verdict_counts == VerdictCounts(2, 1, 1)
    assert result.n_claims == 4


def test_penalized_config_counts_borderline_as_unfaithful():
    llm = FakeLLM(["yes", "yes", "borderline", "no"])

    result = judge_with(llm, penalize=True)("q", "a", CONTEXT)

    assert result.score == pytest.approx(2 / 4)
    assert result.verdict_counts == VerdictCounts(2, 1, 1)


def test_fewer_verdicts_than_claims_keeps_both_counts():
    llm = FakeLLM(["yes", "no"], claims=["a", "b", "c"])

    result = judge_with(llm)("q", "a", CONTEXT)

    assert result.score == pytest.approx(1 / 2)
    assert result.verdict_counts == VerdictCounts(1, 0, 1)
    assert result.n_claims == 3


def test_answer_with_no_claims_scores_one_with_zero_counts():
    result = judge_with(FakeLLM([]))("q", "a", CONTEXT)

    assert result.score == 1.0
    assert type(result.raw_score) is float
    assert result.verdict_counts == VerdictCounts(0, 0, 0)
    assert result.n_claims == 0


def test_claims_with_no_verdicts_are_an_error_not_a_perfect_score():
    with pytest.raises(ValueError, match="no verdicts for 2 claims"):
        judge_with(FakeLLM([], claims=["a", "b"]))("q", "a", CONTEXT)


def test_model_name_always_means_openai_at_temperature_zero(monkeypatch):
    built = []

    def fake_openai_model(**kwargs):
        built.append(kwargs)
        return FakeLLM(["yes"])

    monkeypatch.setattr("deepeval.models.OpenAIModel", fake_openai_model)

    measure_faithfulness("q", "a", ["passage"], model="gpt-4o-mini")

    assert built == [{"model": "gpt-4o-mini", "temperature": 0.0}]


def test_every_passage_reaches_the_metric_in_order():
    llm = FakeLLM(["yes"])

    judge_with(llm)("q", "a", CONTEXT)

    prompt = llm.prompts["Truths"]
    positions = [prompt.index(passage["text"]) for passage in PASSAGES]
    assert positions == sorted(positions)


def test_answer_reaches_the_metric():
    llm = FakeLLM(["yes"])

    judge_with(llm)("q", "The answer under test.", CONTEXT)

    assert "The answer under test." in llm.prompts["Claims"]


def test_passages_are_passed_as_plain_text():
    seen = []

    def measure(input, output, passages):
        seen.append((input, output, passages))
        return 1.0

    DeepEvalFaithfulnessJudge(measure=measure)("q", "a", CONTEXT)

    assert seen == [("q", "a", [passage["text"] for passage in PASSAGES])]


def test_backend_errors_reach_the_caller():
    class DownLLM(FakeLLM):
        def generate(self, prompt, schema=None):
            raise ConnectionError("backend is down")

    with pytest.raises(ConnectionError, match="backend is down"):
        judge_with(DownLLM(["yes"]))("q", "a", CONTEXT)


def test_concurrent_calls_keep_their_own_verdicts():
    def score(yes: int):
        verdicts = ["yes"] * yes + ["no"] * (4 - yes)
        return yes, judge_with(FakeLLM(verdicts))("q", "a", CONTEXT)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(score, [i % 5 for i in range(40)]))

    for yes, result in results:
        assert result.score == pytest.approx(yes / 4)
        assert result.verdict_counts == VerdictCounts(yes, 0, 4 - yes)


def test_reports_model_and_installed_deepeval_version():
    judge = DeepEvalFaithfulnessJudge(measure=lambda *_: 1.0)

    assert judge.model == DEFAULT_MODEL
    assert judge.version == version("deepeval")

    result = judge("q", "a", CONTEXT)
    assert (result.model, result.version) == (DEFAULT_MODEL, version("deepeval"))


def test_configs_are_different_judges():
    default = DeepEvalFaithfulnessJudge(measure=lambda *_: 1.0)
    penalized = DeepEvalFaithfulnessJudge(
        measure=lambda *_: 1.0,
        penalize_ambiguous_claims=True,
    )

    assert default.name == "deepeval-faithfulness"
    assert penalized.name == "deepeval-faithfulness-penalized"


def test_importing_the_adapter_does_not_load_deepeval():
    code = (
        "import sys; import mtlj.judges.deepeval.faithfulness; sys.exit('deepeval' in sys.modules)"
    )

    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


async def test_scores_records_through_the_runner():
    record = DatasetRecord(
        record_id="r1",
        source_dataset="test",
        question="What is the capital of France?",
        context=PASSAGES,
        answer="Paris.",
    )
    sink = MemorySink()

    summary = await run_judge(
        judge_with(FakeLLM(["yes", "no"])),
        [record],
        [],
        sink,
        samples=3,
    )

    assert (summary.ok, summary.errors) == (3, 0)
    for row in sink.evaluations:
        assert row.score == pytest.approx(0.5)
        assert (row.verdict_yes, row.verdict_borderline, row.verdict_no) == (1, 0, 1)
        assert row.n_claims == 2
        assert row.version == version("deepeval")


def test_runner_context_is_what_the_adapter_reads():
    context = judge_context([Passage(passage_id="p1", text="Some text.")])
    seen = []

    DeepEvalFaithfulnessJudge(measure=lambda _i, _o, p: seen.extend(p) or 1.0)("q", "a", context)

    assert seen == ["Some text."]
