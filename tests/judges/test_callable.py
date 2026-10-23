import pytest

from mtlj.judges.base import JudgeResult, RawScore, VerdictCounts
from mtlj.judges.callable import CallableJudge

# --------------------------------------------------
# Scoring tests
# --------------------------------------------------


@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [
        (0.0, 0.0),  # Minimum score
        (0.25, 0.25),  # Quarter of the range
        (1.0, 1.0),  # Maximum score
    ],
)
def test_scores_on_default_range(raw_score, expected):
    judge = CallableJudge(lambda input, output, context: raw_score)

    result = judge(input="input", output="output", context={})

    assert isinstance(result, JudgeResult)
    assert result.score == pytest.approx(expected)
    assert result.raw_score == raw_score


def test_normalizes_custom_score_range():
    judge = CallableJudge(lambda input, output, context: 4, score_range=(1.0, 5.0))

    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(0.75)
    assert result.raw_score == 4.0


def test_passes_arguments_through_unchanged():
    calls = []

    def score_fn(input, output, context):
        calls.append((input, output, context))
        return 0.5

    context = {"source": "unit-test"}
    CallableJudge(score_fn)(input="question", output="answer", context=context)

    assert calls == [("question", "answer", context)]


def test_calls_function_every_time():
    """Repeated scoring must reach the function, not a cached result."""
    calls = []

    def score_fn(input, output, context):
        calls.append(input)
        return 0.5

    judge = CallableJudge(score_fn)
    for _ in range(3):
        judge(input="same", output="same", context={})

    assert len(calls) == 3


# --------------------------------------------------
# Invalid score tests
# --------------------------------------------------


@pytest.mark.parametrize(
    "raw_score",
    [
        -0.1,  # Below minimum
        1.1,  # Above maximum
        float("nan"),  # Not a number
        float("inf"),  # Positive infinity
    ],
)
def test_rejects_out_of_range_scores(raw_score):
    judge = CallableJudge(lambda input, output, context: raw_score)

    with pytest.raises(ValueError):
        judge(input="input", output="output", context={})


def test_rejects_non_callable():
    with pytest.raises(TypeError, match="callable"):
        CallableJudge(0.5)


# --------------------------------------------------
# Higher-is-better tests
# --------------------------------------------------


@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [
        (1.0, 1.0),  # Best raw score
        (2.0, 0.75),
        (5.0, 0.0),  # Worst raw score
    ],
)
def test_flips_score_when_lower_is_better(raw_score, expected):
    judge = CallableJudge(
        lambda input, output, context: raw_score,
        score_range=(1.0, 5.0),
        higher_is_better=False,
    )

    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(expected)
    assert result.raw_score == raw_score


# --------------------------------------------------
# Metadata tests
# --------------------------------------------------


def test_reports_default_metadata():
    result = CallableJudge(lambda input, output, context: 0.5)(
        input="input", output="output", context={}
    )

    assert result.judge_name == "callable-judge"
    assert result.model == "python-callable"
    assert result.version == "1.0.0"
    assert result.score_range == (0.0, 1.0)


def test_reports_custom_metadata():
    judge = CallableJudge(
        lambda input, output, context: 0.5,
        name="keyword-judge",
        model="regex",
        version="2.1.0",
    )

    result = judge(input="input", output="output", context={})

    assert result.judge_name == "keyword-judge"
    assert result.model == "regex"
    assert result.version == "2.1.0"


# --------------------------------------------------
# Verdict count tests
# --------------------------------------------------


def test_plain_float_reports_no_verdicts():
    result = CallableJudge(lambda input, output, context: 0.5)(
        input="input", output="output", context={}
    )

    assert result.verdict_counts is None
    assert result.n_claims is None


def test_raw_score_passes_verdicts_through():
    counts = VerdictCounts(yes=6, borderline=1, no=0)
    judge = CallableJudge(
        lambda input, output, context: RawScore(0.75, verdict_counts=counts, n_claims=7)
    )

    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(0.75)
    assert result.verdict_counts == counts
    assert result.n_claims == 7


def test_rejects_invalid_verdicts_from_function():
    judge = CallableJudge(
        lambda input, output, context: RawScore(
            0.5, verdict_counts=VerdictCounts(5, 2, 1), n_claims=7
        )
    )

    with pytest.raises(ValueError, match="sum"):
        judge(input="input", output="output", context={})
