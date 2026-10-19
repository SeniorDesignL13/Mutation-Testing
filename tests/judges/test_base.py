# tests/test_normalize_score.py

import pytest

from mtlj.judges.base import normalize_score


@pytest.mark.parametrize(
    ("score", "min_score", "max_score", "expected"),
    [
        (1.0, 1.0, 5.0, 0.0),  # Minimum
        (3.0, 1.0, 5.0, 0.5),  # Midpoint
        (5.0, 1.0, 5.0, 1.0),  # Maximum
        (4.0, 1.0, 5.0, 0.75),  # Above midpoint
        (2.0, 1.0, 5.0, 0.25),  # Below midpoint
    ],
)
def test_normalize_score_valid_values(score, min_score, max_score, expected):
    assert normalize_score(score, min_score, max_score) == pytest.approx(expected)


def test_normalize_score_already_normalized():
    """A score in the [0, 1] range remains unchanged."""
    assert normalize_score(0.75, 0.0, 1.0) == pytest.approx(0.75)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (-1.0, 0.0),
        (0.0, 0.0),
        (6.0, 1.0),
        (10.0, 1.0),
    ],
)
def test_normalize_score_clamps_out_of_range_values(score, expected):
    assert normalize_score(score, 0.0, 5.0) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("min_score", "max_score"),
    [
        (0.0, 0.0),
        (5.0, 1.0),
        (3.0, 3.0),
    ],
)
def test_normalize_score_rejects_invalid_ranges(min_score, max_score):
    with pytest.raises(ValueError, match="max_score must be greater than min_score"):
        normalize_score(2.0, min_score, max_score)


def test_normalize_score_supports_negative_declared_range():
    """Normalization should also work when the declared range is negative."""
    assert normalize_score(-5.0, -10.0, 0.0) == pytest.approx(0.5)
