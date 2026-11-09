"""Tests for the multiple-comparison correction defined in docs/metrics.md (MTLJ-36)."""

import math
from collections.abc import Callable
from itertools import pairwise

import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests

from mtlj.stats.correction import (
    CorrectedTest,
    benjamini_hochberg,
    correct_above_chance,
)
from mtlj.stats.detection import DetectionRate, MutantScores, detection_rate
from mtlj.stats.spurious import SpuriousSensitivity, spurious_sensitivity

# What the spike_texts fixture returns: originals by record id, and one type's mutants.
SpikeTexts = tuple[dict[str, list[float]], list[MutantScores]]

# Benjamini and Hochberg (1995), section 4: 15 p-values from a study of myocardial infarction
# treatments. At q = 0.05 the paper's procedure rejects the four smallest; nine are below 0.05.
PAPER_P_VALUES = (
    0.0001,
    0.0004,
    0.0019,
    0.0095,
    0.0201,
    0.0278,
    0.0298,
    0.0344,
    0.0459,
    0.3240,
    0.4262,
    0.5719,
    0.6528,
    0.7590,
    1.000,
)


def by_hand(p_values: list[float]) -> list[float]:
    """Adjusted p-values straight from the definition: min over j >= i of m * p_(j) / j."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, m * p_values[i] / rank)
        adjusted[i] = running
    return adjusted


def test_matches_the_papers_example() -> None:
    result = benjamini_hochberg(dict(enumerate(PAPER_P_VALUES)))

    assert [t.significant_corrected for t in result.tests.values()] == [True] * 4 + [False] * 11
    assert sum(t.significant for t in result.tests.values()) == 9
    assert [t.adjusted for t in result.tests.values()] == pytest.approx(
        by_hand(list(PAPER_P_VALUES)), abs=1e-12
    )
    assert (result.n_tests, result.q, result.method) == (15, 0.05, "benjamini-hochberg")


@pytest.mark.parametrize("seed", range(5))
def test_matches_statsmodels(seed: int) -> None:
    rng = np.random.default_rng(seed)
    # A mix of real effects (near 0) and nulls (uniform), shuffled.
    p_values = rng.permutation(np.concatenate([rng.beta(0.2, 8, 6), rng.uniform(size=14)]))
    result = benjamini_hochberg(dict(enumerate(p_values.tolist())))
    reject, adjusted, _, _ = multipletests(p_values, alpha=0.05, method="fdr_bh")

    assert [t.adjusted for t in result.tests.values()] == pytest.approx(adjusted, abs=1e-12)
    assert [t.significant_corrected for t in result.tests.values()] == list(reject)


def test_correction_can_change_the_verdict() -> None:
    # The illustration in docs/metrics.md: three tests below 0.05, one after correction.
    result = benjamini_hochberg({"a": 0.01, "b": 0.03, "c": 0.04, "d": 0.2})

    assert {k: round(t.adjusted or 0, 4) for k, t in result.tests.items()} == {
        "a": 0.04,
        "b": 0.0533,
        "c": 0.0533,
        "d": 0.2,
    }
    assert [t.significant for t in result.tests.values()] == [True, True, True, False]
    assert [t.significant_corrected for t in result.tests.values()] == [True, False, False, False]


def test_adjusted_is_at_least_p_and_keeps_its_order() -> None:
    rng = np.random.default_rng(1)
    p_values = rng.uniform(size=50).tolist()
    tests = benjamini_hochberg(dict(enumerate(p_values))).tests

    ranked = sorted(tests.values(), key=lambda t: t.p_value or 0)
    assert all((t.adjusted or 0) >= (t.p_value or 0) for t in ranked)
    assert all((a.adjusted or 0) <= (b.adjusted or 0) for a, b in pairwise(ranked))
    assert all((t.adjusted or 0) <= 1 for t in ranked)


def test_a_family_of_one_is_not_changed() -> None:
    test = benjamini_hochberg({"only": 0.03}).tests["only"]

    assert test == CorrectedTest(0.03, 0.03, True, True)


def test_controls_false_discoveries_when_every_null_is_true() -> None:
    # With every null true, any significant result is a false discovery, so the false discovery
    # rate is the chance of at least one. Uncorrected, that's 1 - 0.95^20 = 64%.
    rng = np.random.default_rng(0)
    any_uncorrected = any_corrected = 0
    runs = 4000
    for _ in range(runs):
        tests = benjamini_hochberg(dict(enumerate(rng.uniform(size=20).tolist()))).tests
        any_uncorrected += any(t.significant for t in tests.values())
        any_corrected += any(t.significant_corrected for t in tests.values())

    assert any_uncorrected / runs == pytest.approx(1 - 0.95**20, abs=0.03)
    assert any_corrected / runs == pytest.approx(0.05, abs=0.01)


def test_controls_the_false_discovery_rate_with_real_effects() -> None:
    # 5 real effects among 20 tests. Benjamini-Hochberg bounds the expected share of false
    # discoveries among the significant results by q * 15 / 20 = 3.75%.
    rng = np.random.default_rng(0)
    shares = []
    for _ in range(4000):
        real = rng.beta(0.1, 10, size=5).tolist()
        null = rng.uniform(size=15).tolist()
        tests = benjamini_hochberg(dict(enumerate(real + null))).tests
        called = [i for i, t in tests.items() if t.significant_corrected]
        shares.append(sum(i >= 5 for i in called) / len(called) if called else 0.0)

    assert np.mean(shares) == pytest.approx(0.05 * 15 / 20, abs=0.01)


def test_tests_without_a_p_value_are_left_out_of_the_family() -> None:
    result = benjamini_hochberg({"a": 0.01, "none": None, "b": 0.03})

    assert result.n_tests == 2
    assert result.tests["none"] == CorrectedTest(None, None, False, False)
    # As if "none" weren't there: m = 2.
    assert result.tests["a"].adjusted == pytest.approx(0.02)
    assert result.tests["b"].adjusted == pytest.approx(0.03)


def test_an_empty_family_has_no_tests() -> None:
    result = benjamini_hochberg({})

    assert (result.tests, result.n_tests) == ({}, 0)


def test_q_sets_the_cutoff_both_ways() -> None:
    result = benjamini_hochberg({"a": 0.01, "b": 0.03, "c": 0.04, "d": 0.2}, q=0.10)

    assert [t.significant for t in result.tests.values()] == [True, True, True, False]
    assert [t.significant_corrected for t in result.tests.values()] == [True, True, True, False]


def test_significant_means_strictly_below_q() -> None:
    test = benjamini_hochberg({"edge": 0.05}).tests["edge"]

    assert (test.significant, test.significant_corrected) == (False, False)


@pytest.mark.parametrize("p", [-0.01, 1.01, math.nan])
def test_rejects_a_p_value_outside_zero_to_one(p: float) -> None:
    with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
        benjamini_hochberg({"bad": p})


@pytest.mark.parametrize("q", [0.0, 1.0, -0.5])
def test_rejects_a_q_outside_zero_to_one(q: float) -> None:
    with pytest.raises(ValueError, match="q must be in"):
        benjamini_hochberg({"a": 0.01}, q=q)


@pytest.mark.parametrize(
    ("config", "adjusted"),
    [
        ("default", {"entity_swap": 0.0, "verbosity_pad": 0.0755, "reformat": 0.0039}),
        ("penalize", {"entity_swap": 0.0, "verbosity_pad": 0.0026, "reformat": 0.0026}),
    ],
)
def test_spike_family_matches_worked_example(
    spike_texts: Callable[[str, str], SpikeTexts],
    config: str,
    adjusted: dict[str, float],
) -> None:
    results: dict[str, DetectionRate | SpuriousSensitivity] = {
        "entity_swap": detection_rate(*spike_texts(config, "entity_swap")),
        "verbosity_pad": spurious_sensitivity(*spike_texts(config, "verbosity_pad")),
        "reformat": spurious_sensitivity(*spike_texts(config, "reformat")),
    }
    correction = correct_above_chance(results)

    assert {k: round(t.adjusted or 0, 4) for k, t in correction.tests.items()} == adjusted
    for operator, test in correction.tests.items():
        assert test.p_value == results[operator].p_value
        assert test.significant is results[operator].above_chance
    # Correction changes no verdict on the spike: entity swap and reformatting stay above chance
    # on both configs, and padding only on the penalize one.
    assert [t.significant_corrected for t in correction.tests.values()] == [
        t.significant for t in correction.tests.values()
    ]
    assert (correction.q, correction.n_tests) == (0.05, 3)


def test_results_with_different_alphas_are_rejected(
    spike_texts: Callable[[str, str], SpikeTexts],
) -> None:
    results: dict[str, DetectionRate | SpuriousSensitivity] = {
        "at 5%": detection_rate(*spike_texts("default", "entity_swap")),
        "at 10%": spurious_sensitivity(*spike_texts("default", "reformat"), alpha=0.10),
    }

    with pytest.raises(ValueError, match="different alphas"):
        correct_above_chance(results)


def test_untestable_results_are_left_out() -> None:
    originals = {"a": [0.8, 0.9], "b": [0.7, 0.9]}
    results: dict[str, DetectionRate | SpuriousSensitivity] = {
        "unchanged": spurious_sensitivity(originals, [MutantScores("a", [0.1], changed=False)]),
        "no scores": detection_rate(originals, [MutantScores("b", [])]),
        "testable": spurious_sensitivity(originals, [MutantScores("a", [0.85, 0.85])]),
    }
    correction = correct_above_chance(results)

    assert correction.n_tests == 1
    assert correction.tests["unchanged"] == CorrectedTest(None, None, False, False)
    assert correction.tests["no scores"] == CorrectedTest(None, None, False, False)
    assert correction.tests["testable"].adjusted == results["testable"].p_value
