"""Tests for the record-level bootstrap intervals of detection rate and spurious sensitivity."""

import math
import random
from collections.abc import Callable
from statistics import NormalDist
from types import ModuleType

import numpy as np
import pytest
from scipy.stats import binom

from mtlj.stats import detection, spurious
from mtlj.stats.bootstrap import bootstrap_rate, resampled_rates, summarize
from mtlj.stats.detection import MutantScores, detection_rate, wilson_interval
from mtlj.stats.spurious import spurious_sensitivity

SpikeTexts = tuple[dict[str, list[float]], list[MutantScores]]
RateFunction = Callable[..., detection.DetectionRate | spurious.SpuriousSensitivity]
METRICS: list[tuple[ModuleType, RateFunction]] = [
    (detection, detection_rate),
    (spurious, spurious_sensitivity),
]


def fixed_noise_run(n: int, hits: int) -> tuple[dict[str, list[float]], list[MutantScores]]:
    """A run whose noise floor is the same in every resample, so tau and the verdicts are fixed.

    Every original scores 0.4 and 0.6, so each has the same variance and any mix of them pools
    to the same sigma (0.141; tau is 0.23 one-sided, 0.28 two-sided). The first ``hits`` mutants
    score 0 twice, a drop of 0.5 that both metrics count; the rest score like their original.
    The rate in a resample is then the share of hits drawn, so its bootstrap distribution is
    exactly Binomial(n, hits / n) / n.
    """
    originals = {str(i): [0.4, 0.6] for i in range(n)}
    mutants = [MutantScores(str(i), [0.0, 0.0] if i < hits else [0.4, 0.6]) for i in range(n)]
    return originals, mutants


# --- Validation against a known distribution ------------------------------------------------


@pytest.mark.parametrize(("module", "rate_of"), METRICS)
@pytest.mark.parametrize("level", [0.95, 0.90])
def test_matches_the_binomial_when_tau_is_fixed(
    module: ModuleType, rate_of: RateFunction, level: float
) -> None:
    n, hits = 400, 120
    result = rate_of(*fixed_noise_run(n, hits), level=level)
    assert result.rate == pytest.approx(0.3)
    assert result.bootstrap is not None and result.bootstrap.ci is not None
    low, high = result.bootstrap.ci

    # The bootstrap distribution is Binomial(n, 0.3) / n, whose quantiles are known exactly.
    tail = (1 - level) / 2
    assert low == pytest.approx(binom.ppf(tail, n, 0.3) / n, abs=1 / n)
    assert high == pytest.approx(binom.ppf(1 - tail, n, 0.3) / n, abs=1 / n)
    # And at this size it's close to the normal-approximation interval.
    half = NormalDist().inv_cdf(1 - tail) * math.sqrt(0.3 * 0.7 / n)
    assert (low, high) == pytest.approx((0.3 - half, 0.3 + half), abs=2 / n)


def test_covers_the_true_rate_at_the_nominal_level() -> None:
    # Draw 300 runs of 100 records in which each mutant is a hit with probability 0.3, and count
    # how often the 95% interval contains 0.3. Expect about 95%; 300 runs give a standard error
    # of about 1.3 points.
    rng = random.Random(39)
    covered = 0
    for run in range(300):
        originals = {str(i): [0.4, 0.6] for i in range(100)}
        mutants = [
            MutantScores(str(i), [0.0, 0.0] if rng.random() < 0.3 else [0.4, 0.6])
            for i in range(100)
        ]
        result = detection_rate(originals, mutants, n_resamples=1_000, seed=run)
        assert result.bootstrap is not None and result.bootstrap.ci is not None
        low, high = result.bootstrap.ci
        covered += low <= 0.3 <= high
    assert 0.92 <= covered / 300 <= 0.98


# --- What the bootstrap adds over the Wilson interval ---------------------------------------


@pytest.mark.parametrize(
    ("module", "rate_of", "mutation_type"),
    [
        (detection, detection_rate, "entity_swap"),
        (spurious, spurious_sensitivity, "verbosity_pad"),
        (spurious, spurious_sensitivity, "reformat"),
    ],
)
@pytest.mark.parametrize("config", ["default", "penalize"])
def test_the_original_sample_as_a_resample_reproduces_the_rate(
    spike_texts: Callable[[str, str], SpikeTexts],
    module: ModuleType,
    rate_of: RateFunction,
    mutation_type: str,
    config: str,
) -> None:
    # Drawing every record exactly once is the original sample, so the vectorized rule must give
    # the metric's own rate. This keeps each metric's two forms of its rule in step.
    originals, mutants = spike_texts(config, mutation_type)
    summaries = summarize(originals, mutants)
    once = np.ones((1, len(summaries.ss)))
    rates = resampled_rates(summaries, once, module.resampled_verdict())
    assert rates[0] == rate_of(originals, mutants).rate


def zero_noise_run() -> SpikeTexts:
    """No noise, so tau is 0: a real drop, a real rise, and a delta that is only rounding."""
    originals = {"a": [0.8] * 3, "b": [0.8] * 3, "c": [0.8] * 3}
    mutants = [
        MutantScores("a", [0.75] * 3),
        MutantScores("b", [0.85] * 3),
        MutantScores("c", [0.7, 0.9, 0.8]),  # averages to 0.8 only up to rounding
    ]
    return originals, mutants


def no_headroom_run() -> SpikeTexts:
    """An original whose mean is below tau, which detection rate excludes."""
    originals = {"low": [0.01, 0.0, 0.02], "high": [0.9, 0.8, 1.0]}
    return originals, [MutantScores("low", [0.0] * 3), MutantScores("high", [0.4] * 3)]


def mixed_run() -> SpikeTexts:
    """Unequal score counts, every exclusion, and a record with two mutants."""
    originals = {"a": [1.0, 0.9, 1.0, 0.9, 1.0], "b": [1.0, 0.9, 1.0, 0.9, 1.0], "c": [0.9] * 5}
    sigma = detection.noise_floor(originals.values())
    # Clears tau at 5 vs 5 scores, but not at 5 vs 2.
    drop = (detection.threshold(sigma, 5, 5) + detection.threshold(sigma, 5, 2)) / 2
    mutants = [
        MutantScores("a", [0.96 - drop] * 5),
        MutantScores("b", [0.96 - drop] * 2),
        MutantScores("c", [0.5] * 5, changed=False),
        MutantScores("c", []),
        MutantScores("missing", [0.5] * 5),
    ]
    return originals, mutants


@pytest.mark.parametrize(("module", "rate_of"), METRICS)
@pytest.mark.parametrize("run", [zero_noise_run, no_headroom_run, mixed_run])
def test_the_original_sample_reproduces_the_rate_in_edge_cases(
    module: ModuleType, rate_of: RateFunction, run: Callable[[], SpikeTexts]
) -> None:
    originals, mutants = run()
    summaries = summarize(originals, mutants)
    once = np.ones((1, len(summaries.ss)))
    rates = resampled_rates(summaries, once, module.resampled_verdict())
    assert rates[0] == rate_of(originals, mutants).rate


@pytest.mark.parametrize(
    ("rate_of", "mutation_type", "config", "ci"),
    [
        (detection_rate, "entity_swap", "default", (0.30, 0.80)),
        (spurious_sensitivity, "reformat", "default", (0.05, 0.45)),
        (spurious_sensitivity, "verbosity_pad", "default", (0.00, 0.30)),
    ],
)
def test_spike_intervals_match_worked_example(
    spike_texts: Callable[[str, str], SpikeTexts],
    rate_of: RateFunction,
    mutation_type: str,
    config: str,
    ci: tuple[float, float],
) -> None:
    # docs/metrics.md ("Bootstrap intervals") quotes these. They're on a 0.05 grid (20 mutants),
    # so a change in the generator's stream could move a bound by a step; allow one.
    result = rate_of(*spike_texts(config, mutation_type))
    assert result.bootstrap is not None and result.bootstrap.ci is not None
    assert result.bootstrap.ci == pytest.approx(ci, abs=0.05 + 1e-9)
    assert result.bootstrap.n_valid == result.bootstrap.n_resamples == 10_000


def test_uncertainty_in_tau_widens_the_interval() -> None:
    # Two of 20 originals carry all the noise, and every mutant clears tau by 5%. A resample
    # with fewer noisy originals has a smaller tau and detects them all; one with more has a
    # larger tau and detects none. The Wilson interval sees 20 of 20 and can't know this.
    noisy = [1.0, 0.5, 1.0, 0.5, 1.0]
    originals = {str(i): noisy if i < 2 else [1.0] * 5 for i in range(20)}
    sigma = detection.noise_floor(originals.values())
    drop = 1.05 * detection.threshold(sigma, 5, 5)
    mutants = [MutantScores(r, [sum(scores) / 5 - drop] * 5) for r, scores in originals.items()]

    result = detection_rate(originals, mutants)
    assert result.detected == result.testable == 20
    assert result.ci is not None and result.ci[0] > 0.8
    assert result.bootstrap is not None and result.bootstrap.ci is not None
    assert result.bootstrap.ci[0] < 0.2


# --- Settings and edge cases ----------------------------------------------------------------


@pytest.mark.parametrize(("module", "rate_of"), METRICS)
def test_level_sets_both_intervals(module: ModuleType, rate_of: RateFunction) -> None:
    run = fixed_noise_run(100, 30)
    default = rate_of(*run)
    ninety = rate_of(*run, level=0.90)

    assert default.level == 0.95
    assert ninety.level == 0.90
    assert ninety.ci == wilson_interval(30, 100, 0.90)
    assert default.bootstrap is not None and default.bootstrap.ci is not None
    assert ninety.bootstrap is not None and ninety.bootstrap.ci is not None
    assert ninety.bootstrap.level == 0.90
    assert ninety.bootstrap.ci[0] >= default.bootstrap.ci[0]
    assert ninety.bootstrap.ci[1] <= default.bootstrap.ci[1]


@pytest.mark.parametrize(("module", "rate_of"), METRICS)
def test_same_seed_gives_the_same_interval(module: ModuleType, rate_of: RateFunction) -> None:
    run = fixed_noise_run(50, 20)
    first = rate_of(*run, seed=7, n_resamples=500)
    second = rate_of(*run, seed=7, n_resamples=500)
    assert first.bootstrap == second.bootstrap
    assert first.bootstrap is not None
    assert (first.bootstrap.seed, first.bootstrap.n_resamples) == (7, 500)


@pytest.mark.parametrize(("module", "rate_of"), METRICS)
def test_resamples_without_a_noise_floor_are_left_out(
    module: ModuleType, rate_of: RateFunction
) -> None:
    # Only record 0's original has two scores, so a resample that doesn't draw it has no sigma.
    # That happens with probability (4/5)^5, about a third of the time. Every mutant counts in
    # the resamples that have a sigma, so a resample without one must not count as a rate of 0.
    originals = {"0": [0.4, 0.6], **{str(i): [0.5] for i in range(1, 5)}}
    mutants = [MutantScores(str(i), [0.0]) for i in range(5)]

    result = rate_of(originals, mutants, n_resamples=2_000)
    assert result.rate == 1.0
    assert result.bootstrap is not None
    assert result.bootstrap.ci == (1.0, 1.0)
    assert 0.6 * 2_000 < result.bootstrap.n_valid < 0.75 * 2_000


@pytest.mark.parametrize(("module", "rate_of"), METRICS)
def test_no_testable_mutant_leaves_no_interval(module: ModuleType, rate_of: RateFunction) -> None:
    originals = {"a": [0.4, 0.6]}
    result = rate_of(originals, [MutantScores("a", [0.5, 0.5], changed=False)])
    assert result.rate is None
    assert result.bootstrap is None


def test_an_empty_run_gives_no_interval() -> None:
    result = bootstrap_rate(summarize({}, []), detection.resampled_verdict(), 0.95)
    assert result.ci is None
    assert result.n_valid == 0


@pytest.mark.parametrize("level", [0.0, 1.0, 1.5])
def test_rejects_a_level_outside_zero_to_one(level: float) -> None:
    with pytest.raises(ValueError, match="level"):
        detection_rate(*fixed_noise_run(10, 3), level=level)


def test_rejects_zero_resamples() -> None:
    with pytest.raises(ValueError, match="n_resamples"):
        detection_rate(*fixed_noise_run(10, 3), n_resamples=0)
