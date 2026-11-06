"""Tests for the dose-response curve defined in docs/metrics.md (MTLJ-37)."""

import math

import numpy as np
import pytest
from scipy.special import expit, logit

from mtlj.stats.detection import MutantScores, detection_rate
from mtlj.stats.dose_response import (
    DosePoint,
    DoseResponse,
    dose_points,
    fit_dose_response,
    fit_operator,
    fit_operators,
)
from mtlj.stats.spurious import spurious_sensitivity

ALPHA = 0.05
# Padding's study levels: 50, 100, 200 and 400 words.
PADDING = (0.125, 0.25, 0.5, 1.0)


def true_rate(intensity: float, onset: float, slope: float, benchmark: float = 0.10) -> float:
    """The model's hit rate, written out independently of the module."""
    return ALPHA + (1 - ALPHA) * float(
        expit(logit(benchmark) + slope * math.log(intensity / onset))
    )


def expected_points(onset: float, slope: float, n: int) -> list[DosePoint]:
    """Hit counts exactly as the curve predicts them, rounded."""
    return [DosePoint(d, round(n * true_rate(d, onset, slope)), n) for d in PADDING]


def padding(testable: int, *hits: int) -> list[DosePoint]:
    """Hit counts at padding's four levels, with ``testable`` mutants at each."""
    return [DosePoint(d, h, testable) for d, h in zip(PADDING, hits, strict=True)]


def drawn_points(rng: np.random.Generator, rate: float | None, onset: float = 0.3) -> list:
    """Hit counts drawn at random: at a flat ``rate``, or from the curve when it's None."""
    return [
        DosePoint(d, int(rng.binomial(100, true_rate(d, onset, 3) if rate is None else rate)), 100)
        for d in PADDING
    ]


@pytest.mark.parametrize(("onset", "slope"), [(0.3, 3.0), (0.2, 2.0), (0.6, 5.0), (0.09, 1.5)])
def test_recovers_a_known_curve(onset: float, slope: float) -> None:
    fit = fit_dose_response(expected_points(onset, slope, 10_000))

    assert fit.onset == pytest.approx(onset, rel=0.02)
    assert fit.slope == pytest.approx(slope, rel=0.05)
    assert fit.onset_ci[0] < onset < fit.onset_ci[1]
    assert fit.responds
    assert fit.in_range is (PADDING[0] <= onset <= PADDING[-1])
    assert fit.fit_p_value is not None and fit.fit_p_value > 0.5


def test_interval_covers_the_true_onset_at_the_nominal_level() -> None:
    rng = np.random.default_rng(0)
    runs = 300
    covered = 0
    for _ in range(runs):
        low, high = fit_dose_response(drawn_points(rng, None)).onset_ci
        covered += low <= 0.3 <= high

    assert 0.91 <= covered / runs <= 0.99


def test_a_judge_at_chance_has_no_onset() -> None:
    fit = fit_dose_response([DosePoint(d, 5, 100) for d in PADDING])

    assert fit.onset is None and fit.slope is None
    assert fit.onset_ci[1] == math.inf
    assert not fit.responds and not fit.in_range
    # Not ruled out, but if the judge has an onset, it's beyond the tested intensities.
    assert fit.onset_ci[0] > PADDING[-1]
    assert fit.rate(0.5) == ALPHA


def test_rarely_calls_a_judge_at_chance_responsive() -> None:
    rng = np.random.default_rng(0)
    runs = 300
    responds = sum(fit_dose_response(drawn_points(rng, ALPHA)).responds for _ in range(runs))

    assert responds / runs <= 0.07


def test_no_hits_at_any_intensity_has_no_onset() -> None:
    fit = fit_dose_response([DosePoint(d, 0, 100) for d in PADDING])

    assert fit.onset is None and not fit.responds
    assert fit.onset_ci[0] > PADDING[-1]
    # Fewer hits than chance everywhere: the judge is steadier on these mutants than the floor
    # assumes, and the fit test says the curve doesn't describe them.
    assert fit.fit_p_value is not None and fit.fit_p_value < 0.01


def test_a_step_puts_the_onset_between_its_intensities() -> None:
    fit = fit_dose_response(padding(100, 5, 5, 95, 95))

    assert fit.onset is not None and 0.25 < fit.onset < 0.5
    assert fit.responds and fit.in_range


def test_a_plateau_below_one_fails_the_fit_test() -> None:
    # The curve rises to 1. A judge that never catches more than 60% doesn't follow it.
    fit = fit_dose_response(padding(200, 10, 10, 120, 120))

    assert fit.responds
    assert fit.fit_p_value is not None and fit.fit_p_value < 0.05


def test_every_mutant_hit_puts_the_onset_below_the_range() -> None:
    fit = fit_dose_response([DosePoint(d, 20, 20) for d in PADDING])

    assert fit.onset == 0 and fit.slope is None
    assert fit.onset_ci[0] == 0 and fit.onset_ci[1] < PADDING[0]
    assert fit.responds and not fit.in_range
    assert fit.rate(0.5) == 1.0
    assert fit.rate(0) == ALPHA


def test_rate_at_the_onset_is_the_benchmark() -> None:
    fit = fit_dose_response(expected_points(0.3, 3.0, 1000))

    assert fit.onset is not None
    assert fit.rate(fit.onset) == pytest.approx(ALPHA + (1 - ALPHA) * 0.10)
    assert fit.rate(0) == ALPHA
    assert [fit.rate(d) for d in PADDING] == sorted(fit.rate(d) for d in PADDING)


def test_a_higher_benchmark_moves_the_onset_up_the_curve() -> None:
    points = expected_points(0.3, 3.0, 1000)
    at_10 = fit_dose_response(points)
    at_50 = fit_dose_response(points, benchmark=0.5)

    assert at_10.onset is not None and at_50.onset is not None
    # On the same curve, ER = 50% is where logit(0.5) - logit(0.1) = slope * ln(D50 / D10).
    assert at_50.onset == pytest.approx(0.3 * math.exp(-logit(0.1) / 3.0), rel=0.02)
    assert at_50.benchmark == 0.5


def test_level_sets_the_interval() -> None:
    points = expected_points(0.3, 3.0, 100)
    at_95 = fit_dose_response(points)
    at_90 = fit_dose_response(points, level=0.90)

    assert at_95.onset == at_90.onset
    assert at_95.onset_ci[0] < at_90.onset_ci[0] < at_90.onset_ci[1] < at_95.onset_ci[1]
    assert at_90.level == 0.90


def test_two_intensities_have_no_fit_test() -> None:
    fit = fit_dose_response([DosePoint(0.5, 2, 20), DosePoint(1.0, 9, 20)])

    assert fit.onset is not None
    assert fit.fit_p_value is None


def test_worked_example() -> None:
    # docs/metrics.md: padding at 50, 100, 200 and 400 words, 100 testable mutants at each,
    # drawn from a curve with its onset at 80 words (intensity 0.2) and slope 2.
    fit = fit_dose_response(padding(100, 10, 17, 45, 71))

    assert fit.onset is not None
    assert round(fit.onset * 400) == 78
    assert (round(fit.onset_ci[0] * 400), round(fit.onset_ci[1] * 400)) == (56, 102)
    assert round(fit.slope or 0, 2) == 1.89
    assert round(fit.fit_p_value or 0, 2) == 0.70
    assert [round(fit.rate(d) * 100) for d in PADDING] == [9, 19, 42, 72]


def test_worked_example_at_chance() -> None:
    fit = fit_dose_response(padding(100, 5, 9, 3, 9))

    assert not fit.responds
    assert round(fit.onset_ci[0] * 400) == 393
    assert fit.onset_ci[1] == math.inf


def test_points_without_testable_mutants_are_left_out() -> None:
    fit = fit_dose_response([*expected_points(0.3, 3.0, 1000), DosePoint(0.75, 0, 0)])

    assert [p.intensity for p in fit.points] == list(PADDING)


def test_points_are_fitted_in_any_order() -> None:
    points = expected_points(0.3, 3.0, 1000)

    assert fit_dose_response(points[::-1]) == fit_dose_response(points)


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ([DosePoint(1.0, 5, 20)], "two or more intensities"),
        ([DosePoint(0.5, 5, 20), DosePoint(1.0, 0, 0)], "two or more intensities"),
        ([DosePoint(0.5, 5, 20), DosePoint(0.5, 6, 20)], "distinct"),
        ([DosePoint(0.5, 21, 20), DosePoint(1.0, 5, 20)], "hits <= testable"),
        ([DosePoint(0.5, -1, 20), DosePoint(1.0, 5, 20)], "hits <= testable"),
        ([DosePoint(0.0, 1, 20), DosePoint(1.0, 5, 20)], "intensity must be"),
        ([DosePoint(math.nan, 1, 20), DosePoint(1.0, 5, 20)], "intensity must be"),
    ],
)
def test_rejects_points_it_cant_fit(points: list[DosePoint], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        fit_dose_response(points)


@pytest.mark.parametrize("name", ["alpha", "benchmark", "level"])
@pytest.mark.parametrize("value", [0.0, 1.0])
def test_rejects_an_argument_outside_zero_to_one(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=f"{name} must be in"):
        fit_dose_response(expected_points(0.3, 3.0, 100), **{name: value})


def test_rejects_a_negative_intensity_for_the_rate() -> None:
    fit = fit_dose_response(expected_points(0.3, 3.0, 100))

    with pytest.raises(ValueError, match="intensity must be"):
        fit.rate(-0.1)


# Ten originals that score 0.9 or 1.0. A mutant at 0.0 is a big drop, and one at 1.0 or 0.9 the
# same as its original, so which mutants count as hits is clear for both metrics.
ORIGINALS = {f"r{i}": [0.9, 1.0, 0.9, 1.0] for i in range(10)}


def mutants(hits: int) -> list[MutantScores]:
    return [
        MutantScores(f"r{i}", [0.0] * 4 if i < hits else [0.9, 1.0, 0.9, 1.0]) for i in range(10)
    ]


def test_dose_points_count_detected_and_flagged_mutants() -> None:
    detected = {
        0.5: detection_rate(ORIGINALS, mutants(3)),
        1.0: detection_rate(ORIGINALS, mutants(7)),
    }
    flagged = {
        1.0: spurious_sensitivity(ORIGINALS, mutants(6)),
        0.25: spurious_sensitivity(ORIGINALS, mutants(1)),
    }

    assert dose_points(detected) == (DosePoint(0.5, 3, 10), DosePoint(1.0, 7, 10))
    assert dose_points(flagged) == (DosePoint(0.25, 1, 10), DosePoint(1.0, 6, 10))


def test_fit_operator_takes_alpha_from_the_rates() -> None:
    results = {
        d: detection_rate(ORIGINALS, mutants(hits), alpha=0.10)
        for d, hits in zip(PADDING, (1, 2, 5, 9), strict=True)
    }
    fit = fit_operator(results)

    assert fit.alpha == 0.10
    assert fit == fit_dose_response(dose_points(results), alpha=0.10)


def test_fit_operator_rejects_rates_with_different_alphas() -> None:
    results = {
        0.5: detection_rate(ORIGINALS, mutants(3)),
        1.0: detection_rate(ORIGINALS, mutants(7), alpha=0.10),
    }

    with pytest.raises(ValueError, match="different alphas"):
        fit_operator(results)


def test_fit_operators_skips_operators_at_one_intensity() -> None:
    results = {
        "verbosity_padding": {
            d: spurious_sensitivity(ORIGINALS, mutants(hits))
            for d, hits in zip(PADDING, (0, 1, 4, 8), strict=True)
        },
        "reformat": {1.0: spurious_sensitivity(ORIGINALS, mutants(2))},
    }
    fits = fit_operators(results)

    assert list(fits) == ["verbosity_padding"]
    assert isinstance(fits["verbosity_padding"], DoseResponse)
    assert fits["verbosity_padding"].responds
