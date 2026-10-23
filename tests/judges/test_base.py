import math

import pytest

from mtlj.judges.base import Context, Judge, JudgeResult, RawScore, VerdictCounts


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


# --------------------------------------------------
# Verdict count tests
# --------------------------------------------------


class VerdictJudge(DummyJudge):
    """Judge that reports claim verdicts alongside its score."""

    def __init__(self, raw: RawScore):
        self.raw = raw

    def score_raw(
        self,
        input: str,
        output: str,
        context: Context,
    ) -> RawScore:
        return self.raw


def make_result(verdict_counts, n_claims) -> JudgeResult:
    return JudgeResult(
        score=0.5,
        raw_score=0.5,
        score_range=(0.0, 1.0),
        judge_name="TestJudge",
        model="dummy-model",
        version="1.0",
        verdict_counts=verdict_counts,
        n_claims=n_claims,
    )


def test_plain_float_judge_reports_no_verdicts():
    result = DummyJudge(5.0)(input="input", output="output", context={})

    assert result.verdict_counts is None
    assert result.n_claims is None


def test_raw_score_passes_verdicts_through():
    counts = VerdictCounts(yes=6, borderline=1, no=0)
    judge = VerdictJudge(RawScore(7.5, verdict_counts=counts, n_claims=7))

    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(0.75)
    assert result.raw_score == 7.5
    assert result.verdict_counts == counts
    assert result.n_claims == 7


def test_raw_score_without_verdicts_matches_plain_float():
    result = VerdictJudge(RawScore(7.5))(input="input", output="output", context={})

    assert result.score == pytest.approx(0.75)
    assert result.verdict_counts is None
    assert result.n_claims is None


def test_lower_is_better_flips_score_but_not_verdicts():
    class LowerIsBetterVerdictJudge(VerdictJudge):
        higher_is_better = False

    counts = VerdictCounts(yes=2, borderline=0, no=1)
    judge = LowerIsBetterVerdictJudge(RawScore(2.5, verdict_counts=counts, n_claims=3))

    result = judge(input="input", output="output", context={})

    assert result.score == pytest.approx(0.75)
    assert result.verdict_counts == counts


def test_raw_score_out_of_range_is_rejected():
    judge = VerdictJudge(RawScore(11.0, verdict_counts=VerdictCounts(1, 0, 0), n_claims=1))

    with pytest.raises(ValueError):
        judge(input="input", output="output", context={})


def test_claim_count_without_verdicts_is_allowed():
    """A judge can count claims without ruling on each one (e.g. Ollama)."""
    result = make_result(verdict_counts=None, n_claims=4)

    assert result.n_claims == 4
    assert result.verdict_counts is None


@pytest.mark.parametrize(
    ("verdict_counts", "n_claims"),
    [
        (VerdictCounts(5, 2, 0), 7),  # Sums to n_claims
        (VerdictCounts(13, 1, 0), 15),  # DeepEval skipped a verdict (spike record 17388)
        (VerdictCounts(0, 0, 0), 0),  # No claims extracted
    ],
)
def test_accepts_valid_verdict_counts(verdict_counts, n_claims):
    result = make_result(verdict_counts, n_claims)

    assert result.verdict_counts == verdict_counts
    assert result.n_claims == n_claims


@pytest.mark.parametrize(
    "verdict_counts",
    [
        (6, 1, 0),  # Plain tuple
        [6, 1, 0],  # List
    ],
)
def test_plain_sequence_is_stored_as_verdict_counts(verdict_counts):
    result = make_result(verdict_counts, n_claims=7)

    assert isinstance(result.verdict_counts, VerdictCounts)
    assert result.verdict_counts.yes == 6
    assert result.verdict_counts._asdict() == {"yes": 6, "borderline": 1, "no": 0}
    hash(result)  # Frozen result stays hashable


def test_raw_score_with_list_counts_is_stored_as_verdict_counts():
    judge = VerdictJudge(RawScore(7.5, verdict_counts=[6, 1, 0], n_claims=7))

    result = judge(input="input", output="output", context={})

    assert result.verdict_counts == VerdictCounts(yes=6, borderline=1, no=0)
    assert isinstance(result.verdict_counts, VerdictCounts)


@pytest.mark.parametrize(
    ("verdict_counts", "n_claims", "match"),
    [
        (VerdictCounts(5, 2, 1), 7, "sum"),  # More verdicts than claims
        (VerdictCounts(-1, 2, 0), 7, "verdict_counts"),  # Negative count
        (VerdictCounts(True, 0, 0), 1, "verdict_counts"),  # Bool is not a count
        (VerdictCounts(1.0, 0, 0), 1, "verdict_counts"),  # Float is not a count
        ((1, 0), 1, "verdict_counts"),  # Wrong length
        ([1, 0, 0, 0], 1, "verdict_counts"),  # Wrong length list
        (7, 7, "verdict_counts"),  # Total count instead of three counts
        ("yes", 3, "verdict_counts"),  # String of length 3
        ({"yes": 1, "borderline": 0, "no": 0}, 1, "verdict_counts"),  # Dict
        ([1, -1, 0], 1, "verdict_counts"),  # Negative count in a list
        (VerdictCounts(1, 0, 0), None, "n_claims"),  # Verdicts without a claim count
        (None, -1, "n_claims"),  # Negative claim count
        (None, True, "n_claims"),  # Bool is not a claim count
    ],
)
def test_rejects_invalid_verdict_counts(verdict_counts, n_claims, match):
    with pytest.raises(ValueError, match=match):
        make_result(verdict_counts, n_claims)


# --------------------------------------------------
# Raw score type tests
# --------------------------------------------------


@pytest.mark.parametrize(
    "raw_score",
    [
        True,  # Bool would otherwise become 1.0
        False,  # Bool would otherwise become 0.0
        "5.0",  # Numeric string
        None,  # Missing score
        [5.0],  # Container
    ],
)
def test_rejects_non_numeric_raw_scores(raw_score):
    judge = DummyJudge(raw_score)

    with pytest.raises(TypeError, match="TestJudge"):
        judge(input="input", output="output", context={})


def test_rejects_non_numeric_raw_score_inside_raw_score():
    judge = VerdictJudge(RawScore(True))

    with pytest.raises(TypeError, match="real number"):
        judge(input="input", output="output", context={})


def test_accepts_int_raw_score_as_float():
    result = DummyJudge(5)(input="input", output="output", context={})

    assert result.raw_score == 5.0
    assert isinstance(result.raw_score, float)
    assert isinstance(result.score, float)


def test_rejects_int_too_large_for_float():
    """10**400 overflows float(); it must be reported as out of range, not crash."""
    judge = DummyJudge(10**400)

    with pytest.raises(ValueError, match="expected a finite score"):
        judge(input="input", output="output", context={})
