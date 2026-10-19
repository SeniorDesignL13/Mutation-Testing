"""Tests for the detection rate defined in docs/metrics.md."""

import csv
import math
from collections import defaultdict
from pathlib import Path

import pytest

from mtlj.stats.detection import (
    DetectionRate,
    ExclusionReason,
    MutantOutcome,
    MutantScores,
    detection_rate,
    noise_floor,
    threshold,
    wilson_interval,
)

SPIKE_SCORES = Path(__file__).parents[2] / "spike" / "scores.csv"

# The worked example's table in docs/metrics.md: record -> (delta, detected).
WORKED_EXAMPLE = {
    "13122": (0.167, True),
    "15450": (0.167, True),
    "16980": (0.143, True),
    "17622": (0.100, True),
    "13302": (0.000, False),
    "17388": (0.070, True),
    "13560": (0.000, False),
    "15204": (0.000, False),
    "14922": (0.057, True),
    "12660": (0.000, False),
    "17148": (0.125, True),
    "12852": (0.091, True),
    "17490": (-0.006, False),
    "17028": (0.136, True),
    "11868": (0.000, False),
    "17592": (0.279, True),
    "14352": (0.033, False),
    "17472": (-0.029, False),
    "12018": (0.167, True),
    "16080": (0.250, True),
}


def spike_rate(config: str, mutation_type: str) -> DetectionRate:
    """Detection rate of one mutation type in the MTLJ-31 spike's scores."""
    scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    with SPIKE_SCORES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["config"] == config:
                scores[(row["record_id"], row["mutation_type"])].append(float(row["score"]))
    originals = {r: s for (r, kind), s in scores.items() if kind == "original"}
    mutants = [MutantScores(r, s) for (r, kind), s in scores.items() if kind == mutation_type]
    return detection_rate(originals, mutants)


def outcome_of(result: DetectionRate, record_id: str) -> MutantOutcome:
    return next(o for o in result.outcomes if o.record_id == record_id)


@pytest.mark.parametrize(
    ("config", "sigma", "tau", "detected", "ci_percent"),
    [("default", 0.0536, 0.0558, 12, (39, 78)), ("penalize", 0.0619, 0.0644, 11, (34, 74))],
)
def test_spike_entity_swaps_match_worked_example(
    config: str, sigma: float, tau: float, detected: int, ci_percent: tuple[int, int]
) -> None:
    result = spike_rate(config, "entity_swap")

    assert round(result.sigma, 4) == sigma
    assert round(result.tau, 4) == tau
    assert (result.detected, result.testable) == (detected, 20)
    assert result.rate == detected / 20
    assert result.ci is not None
    assert (round(result.ci[0] * 100), round(result.ci[1] * 100)) == ci_percent
    assert result.excluded == dict.fromkeys(ExclusionReason, 0)
    assert (result.n_samples, result.alpha) == (5, 0.05)


def test_spike_entity_swap_verdicts_match_worked_example_table() -> None:
    result = spike_rate("default", "entity_swap")

    for record_id, (delta, detected) in WORKED_EXAMPLE.items():
        outcome = outcome_of(result, record_id)
        assert outcome.delta == pytest.approx(delta, abs=5e-4), record_id
        assert outcome.detected is detected, record_id


@pytest.mark.parametrize(("mutation_type", "flagged"), [("verbosity_pad", 1), ("reformat", 2)])
def test_spike_preserving_mutants_rarely_flagged(mutation_type: str, flagged: int) -> None:
    assert spike_rate("default", mutation_type).detected == flagged


def test_noise_floor_weights_variances_by_degrees_of_freedom() -> None:
    # Variances 0.125 (df 1) and 0.04 (df 2); the single score carries no df and is skipped.
    sigma = noise_floor([[1.0, 0.5], [0.2, 0.4, 0.6], [0.3]])

    assert sigma == pytest.approx(math.sqrt((0.125 + 2 * 0.04) / 3))


def test_noise_floor_needs_an_original_with_two_scores() -> None:
    with pytest.raises(ValueError, match="two or more scores"):
        noise_floor([[0.9], [], [1.0]])


@pytest.mark.parametrize(("n", "ratio"), [(2, 1.64), (3, 1.34), (5, 1.04), (10, 0.74)])
def test_threshold_shrinks_with_more_samples_as_documented(n: int, ratio: float) -> None:
    assert round(threshold(1.0, n, n), 2) == ratio


def test_zero_noise_counts_any_real_drop() -> None:
    originals = {"a": [0.8] * 3, "b": [0.8] * 3, "c": [0.8] * 3}
    mutants = [
        MutantScores("a", [0.75] * 3),
        MutantScores("b", [0.8] * 3),
        MutantScores("c", [1.0] * 3),
    ]

    result = detection_rate(originals, mutants)

    assert (result.sigma, result.tau) == (0.0, 0.0)
    assert [o.detected for o in result.outcomes] == [True, False, False]
    assert (result.detected, result.testable) == (1, 3)


def test_zero_noise_ignores_rounding_in_the_means() -> None:
    # 0.7, 0.9 and 0.8 average to 0.8, but in floating point delta comes out just above 0.
    result = detection_rate({"a": [0.8] * 3}, [MutantScores("a", [0.7, 0.9, 0.8])])
    outcome = outcome_of(result, "a")

    assert result.tau == 0.0
    assert outcome.delta is not None
    assert 0 < outcome.delta < 1e-12
    assert not outcome.detected


def test_original_at_or_below_tau_has_no_headroom() -> None:
    originals = {"low": [0.01, 0.0, 0.02], "high": [0.9, 0.8, 1.0]}
    mutants = [MutantScores("low", [0.0] * 3), MutantScores("high", [0.4] * 3)]

    result = detection_rate(originals, mutants)

    assert outcome_of(result, "low").excluded is ExclusionReason.NO_HEADROOM
    assert result.excluded[ExclusionReason.NO_HEADROOM] == 1
    assert (result.detected, result.testable) == (1, 1)


def test_zero_noise_original_at_zero_has_no_headroom() -> None:
    result = detection_rate({"a": [0.0] * 3}, [MutantScores("a", [0.0] * 3)])

    assert outcome_of(result, "a").excluded is ExclusionReason.NO_HEADROOM
    assert result.testable == 0


def test_unequal_n_uses_each_mutants_own_counts() -> None:
    originals = {"a": [1.0, 0.9, 1.0, 0.9, 1.0], "b": [1.0, 0.9, 1.0, 0.9, 1.0]}
    sigma = noise_floor(originals.values())
    # A drop that clears tau at 5 vs 5 scores, but not at 5 vs 2.
    drop = (threshold(sigma, 5, 5) + threshold(sigma, 5, 2)) / 2
    mean = sum(originals["a"]) / 5
    mutants = [MutantScores("a", [mean - drop] * 5), MutantScores("b", [mean - drop] * 2)]

    result = detection_rate(originals, mutants)

    assert outcome_of(result, "a").tau == pytest.approx(threshold(sigma, 5, 5))
    assert outcome_of(result, "b").tau == pytest.approx(threshold(sigma, 5, 2))
    assert [o.detected for o in result.outcomes] == [True, False]
    assert result.n_samples == 5
    assert result.tau == pytest.approx(threshold(sigma, 5, 5))


def test_all_excluded_gives_no_rate() -> None:
    originals = {"a": [1.0, 0.9], "b": [1.0, 0.9]}
    mutants = [
        MutantScores("a", [], changed=False),
        MutantScores("b", []),
        MutantScores("missing", [0.5, 0.5]),
    ]

    result = detection_rate(originals, mutants)

    assert (result.rate, result.ci) == (None, None)
    assert (result.detected, result.testable) == (0, 0)
    assert result.excluded == {
        ExclusionReason.NO_CHANGE: 1,
        ExclusionReason.NO_SCORES: 2,
        ExclusionReason.NO_HEADROOM: 0,
    }


def test_wilson_interval_stays_inside_zero_and_one() -> None:
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0
    with pytest.raises(ValueError):
        wilson_interval(0, 0)


@pytest.mark.parametrize(("sigma", "n", "alpha"), [(-0.1, 5, 0.05), (0.1, 0, 0.05), (0.1, 5, 0.5)])
def test_threshold_rejects_invalid_input(sigma: float, n: int, alpha: float) -> None:
    with pytest.raises(ValueError):
        threshold(sigma, n, n, alpha)
