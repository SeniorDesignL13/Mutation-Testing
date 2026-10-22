import math

import pytest

from mtlj.judges.base import Context, Judge, JudgeResult


class DummyJudge(Judge):
    """Deterministic judge for testing score normalization."""

    name = "TestJudge"
    model = "dummy-model"
    version = "1.0"
    score_range = (0.0, 10.0)
    higher_is_better = True

    def __init__(self, raw_score: float):
        self.raw_score = raw_score

    def score_raw(
        self,
        input: str,
        output: str,
        context: Context,
    ) -> float:
        return self.raw_score


class LowerIsBetterJudge(DummyJudge):
    """Judge where lower raw scores indicate better results."""

    higher_is_better = False


# --------------------------------------------------
# Normalization tests
# --------------------------------------------------


@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [
        (0.0, 0.0),  # Minimum score
        (2.5, 0.25),  # Quarter of the range
        (5.0, 0.5),  # Midpoint
        (7.5, 0.75),  # Three-quarters of the range
        (10.0, 1.0),  # Maximum score
    ],
)
def test_normalizes_score_correctly(raw_score, expected):
    judge = DummyJudge(raw_score)

    result = judge(
        input="What is 2 + 2?",
        output="4",
        context={},
    )

    assert isinstance(result, JudgeResult)
    assert result.score == pytest.approx(expected)
    assert result.raw_score == raw_score
    assert result.score_range == (0.0, 10.0)


def test_normalizes_nonzero_score_range():
    """Normalization should work when the range doesn't start at zero."""

    class CustomRangeJudge(DummyJudge):
        score_range = (2.0, 6.0)

    judge = CustomRangeJudge(4.0)
    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(0.5)
    assert result.raw_score == 4.0
    assert result.score_range == (2.0, 6.0)


# --------------------------------------------------
# Higher-is-better tests
# --------------------------------------------------


@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [
        (0.0, 1.0),
        (2.5, 0.75),
        (5.0, 0.5),
        (7.5, 0.25),
        (10.0, 0.0),
    ],
)
def test_flips_score_when_lower_is_better(raw_score, expected):
    judge = LowerIsBetterJudge(raw_score)

    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(expected)
    assert result.raw_score == raw_score


def test_higher_is_better_does_not_flip_score():
    result = DummyJudge(8.0)(input="input", output="output", context={})

    assert result.score == pytest.approx(0.8)


# --------------------------------------------------
# Invalid raw score tests
# --------------------------------------------------


@pytest.mark.parametrize(
    "raw_score",
    [
        -1.0,  # Below minimum
        11.0,  # Above maximum
        float("nan"),  # Not a number
        float("inf"),  # Positive infinity
        float("-inf"),  # Negative infinity
    ],
)
def test_rejects_invalid_raw_scores(raw_score):
    judge = DummyJudge(raw_score)

    with pytest.raises(ValueError):
        judge(input="input", output="output", context={})


# --------------------------------------------------
# Invalid score range tests
# --------------------------------------------------


@pytest.mark.parametrize(
    "score_range",
    [
        (0.0, 0.0),  # Equal bounds
        (10.0, 0.0),  # Reversed bounds
        (float("-inf"), 10.0),  # Infinite lower bound
        (0.0, float("inf")),  # Infinite upper bound
        (float("nan"), 10.0),  # NaN lower bound
        (0.0, float("nan")),  # NaN upper bound
    ],
)
def test_rejects_invalid_score_ranges(score_range):
    judge = DummyJudge(5.0)
    judge.score_range = score_range

    with pytest.raises(ValueError, match="score_range"):
        judge(input="input", output="output", context={})


# --------------------------------------------------
# Metadata tests
# --------------------------------------------------


def test_result_contains_judge_metadata():
    result = DummyJudge(7.5)(
        input="input",
        output="output",
        context={"source": "unit-test"},
    )

    assert result.judge_name == "TestJudge"
    assert result.model == "dummy-model"
    assert result.version == "1.0"
    assert result.score_range == (0.0, 10.0)


def test_result_score_is_always_between_zero_and_one():
    for raw_score in range(11):
        result = DummyJudge(float(raw_score))(
            input="input",
            output="output",
            context={},
        )

        assert 0.0 <= result.score <= 1.0
        assert math.isfinite(result.score)
