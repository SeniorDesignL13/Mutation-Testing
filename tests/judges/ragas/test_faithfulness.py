"""The Ragas adapter, run through the real metric with a fake LLM (no network)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from importlib.metadata import version

import pytest

from mtlj.judges.base import VerdictCounts
from mtlj.judges.ragas.faithfulness import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    RagasFaithfulnessJudge,
    _import_metric,
    measure_faithfulness,
)

_import_metric()  # Makes ragas importable before the imports below.

from ragas.llms.base import InstructorBaseRagasLLM  # noqa: E402
from ragas.metrics.collections.faithfulness.util import (  # noqa: E402
    NLIStatementOutput,
    StatementGeneratorOutput,
)

PASSAGES = [
    {"passage_id": "p1", "text": "Paris is the capital of France.", "title": "Paris"},
    {"passage_id": "p2", "text": "France is in Western Europe.", "title": None},
    {"passage_id": "p3", "text": "The Seine flows through Paris.", "title": None},
]
CONTEXT = {"passages": PASSAGES}


class FakeLLM(InstructorBaseRagasLLM):
    """Answers the metric's two prompts with canned statements and verdicts."""

    def __init__(self, verdicts: list[int], statements: list[str] | None = None) -> None:
        self.verdicts = verdicts
        self.statements = ["a claim"] * len(verdicts) if statements is None else statements
        self.prompts: dict[str, str] = {}

    def generate(self, prompt, response_model):
        raise AssertionError("the metric runs async")

    async def agenerate(self, prompt, response_model):
        self.prompts[response_model.__name__] = prompt
        if response_model is StatementGeneratorOutput:
            return StatementGeneratorOutput(statements=self.statements)
        assert response_model is NLIStatementOutput
        return NLIStatementOutput(
            statements=[
                {"statement": "a claim", "reason": "because", "verdict": verdict}
                for verdict in self.verdicts
            ]
        )


def judge_with(llm: FakeLLM) -> RagasFaithfulnessJudge:
    return RagasFaithfulnessJudge(measure=partial(measure_faithfulness, model="fake", llm=llm))


def test_score_and_verdict_counts_come_from_the_metric():
    result = judge_with(FakeLLM([1, 1, 0, 0]))("q", "a", CONTEXT)

    assert result.score == pytest.approx(2 / 4)
    assert result.verdict_counts == VerdictCounts(2, 0, 2)
    assert result.n_claims == 4


def test_borderline_is_always_zero():
    result = judge_with(FakeLLM([1, 0, 1]))("q", "a", CONTEXT)

    assert result.verdict_counts.borderline == 0


def test_fewer_verdicts_than_statements_keeps_both_counts():
    result = judge_with(FakeLLM([1, 0], statements=["a", "b", "c"]))("q", "a", CONTEXT)

    assert result.score == pytest.approx(1 / 2)  # Ragas scores over the verdicts it got.
    assert result.verdict_counts == VerdictCounts(1, 0, 1)
    assert result.n_claims == 3


def test_more_verdicts_than_statements_report_no_counts():
    result = judge_with(FakeLLM([1, 1, 0], statements=["a", "b"]))("q", "a", CONTEXT)

    assert result.score == pytest.approx(2 / 3)
    assert result.verdict_counts is None
    assert result.n_claims is None


def test_answer_with_no_statements_is_an_error_not_a_score():
    with pytest.raises(ValueError, match="no statements"):
        judge_with(FakeLLM([], statements=[]))("q", "a", CONTEXT)


class _WordEncoding:
    """Stands in for tiktoken's encoding, which downloads its tables on first use and so can't
    run with the network blocked. One token per word is enough to test the counting."""

    def encode(self, text):
        return text.split()


def test_on_usage_gets_the_estimated_tokens_once_per_call_even_when_it_fails(monkeypatch):
    import tiktoken

    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: _WordEncoding())
    usage = []

    measure_faithfulness(
        "q", "a", ["passage"], model="fake", llm=FakeLLM([1]), on_usage=lambda *u: usage.append(u)
    )

    class DownLLM(FakeLLM):
        async def agenerate(self, prompt, response_model):
            raise ConnectionError("backend is down")

    with pytest.raises(ConnectionError):
        measure_faithfulness(
            "q", "a", ["p"], model="fake", llm=DownLLM([1]), on_usage=lambda *u: usage.append(u)
        )

    assert len(usage) == 2
    assert all(prompt > 0 for prompt, _ in usage)
    assert usage[0][1] > 0  # The answered call has completion tokens.
    assert usage[1][1] == 0  # The failed one was billed for its prompt only.


def test_every_passage_reaches_the_metric_in_order():
    llm = FakeLLM([1])

    judge_with(llm)("q", "a", CONTEXT)

    prompt = llm.prompts["NLIStatementOutput"]
    positions = [prompt.index(passage["text"]) for passage in PASSAGES]
    assert positions == sorted(positions)


def test_question_and_answer_reach_the_metric():
    llm = FakeLLM([1])

    judge_with(llm)("The question?", "The answer under test.", CONTEXT)

    prompt = llm.prompts["StatementGeneratorOutput"]
    assert "The question?" in prompt
    assert "The answer under test." in prompt


def test_passages_are_passed_as_plain_text():
    seen = []

    def measure(input, output, passages):
        seen.append((input, output, passages))
        return 1.0

    RagasFaithfulnessJudge(measure=measure)("q", "a", CONTEXT)

    assert seen == [("q", "a", [passage["text"] for passage in PASSAGES])]


def test_backend_errors_reach_the_caller():
    class DownLLM(FakeLLM):
        async def agenerate(self, prompt, response_model):
            raise ConnectionError("backend is down")

    with pytest.raises(ConnectionError, match="backend is down"):
        judge_with(DownLLM([1]))("q", "a", CONTEXT)


def test_concurrent_calls_keep_their_own_verdicts():
    def score(yes: int):
        verdicts = [1] * yes + [0] * (4 - yes)
        return yes, judge_with(FakeLLM(verdicts))("q", "a", CONTEXT)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(score, [i % 5 for i in range(40)]))

    for yes, result in results:
        assert result.score == pytest.approx(yes / 4)
        assert result.verdict_counts == VerdictCounts(yes, 0, 4 - yes)


def test_llm_is_built_with_the_judges_settings(monkeypatch):
    built = []
    fake = FakeLLM([1])

    def fake_factory(model, **kwargs):
        built.append((model, kwargs))
        return fake

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr("ragas.llms.llm_factory", fake_factory)

    measure_faithfulness("q", "a", ["p"], model="gpt-4o", temperature=0.2, max_tokens=99)

    ((model, kwargs),) = built
    assert model == "gpt-4o"
    assert (kwargs["temperature"], kwargs["max_tokens"]) == (0.2, 99)


def test_reports_name_model_and_installed_ragas_version():
    judge = RagasFaithfulnessJudge(measure=lambda *_: 1.0)

    assert judge.name == "ragas-faithfulness"
    assert judge.model == DEFAULT_MODEL == "gpt-4o-mini"
    assert judge.version == version("ragas")

    result = judge("q", "a", CONTEXT)
    assert (result.model, result.version) == (DEFAULT_MODEL, version("ragas"))


def test_defaults_are_the_ones_ragas_ships_with():
    from ragas.llms.base import InstructorModelArgs

    shipped = InstructorModelArgs()

    assert shipped.temperature == DEFAULT_TEMPERATURE


def test_cache_settings_cover_every_setting_that_changes_the_score():
    default = RagasFaithfulnessJudge(measure=lambda *_: 1.0)
    hot = RagasFaithfulnessJudge(measure=lambda *_: 1.0, temperature=0.7)
    long = RagasFaithfulnessJudge(measure=lambda *_: 1.0, max_tokens=8192)

    assert default.cache_settings() == {
        "temperature": DEFAULT_TEMPERATURE,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    assert hot.cache_settings() != default.cache_settings()
    assert long.cache_settings() != default.cache_settings()
