"""Tests for the paired original-versus-mutant comparison (MTLJ-34)."""

import math
from collections.abc import Callable

import pytest

from mtlj.stats.detection import (
    DetectionRate,
    ExclusionReason,
    MutantOutcome,
    MutantScores,
    detection_rate,
)
from mtlj.stats.paired import PairedDelta, effect_size, paired_delta


def outcome(
    record_id: str, delta: float | None, excluded: ExclusionReason | None = None
) -> MutantOutcome:
    return MutantOutcome(record_id, False, excluded, delta, None if delta is None else 0.05)


# The spike's results tables (docs/spike-effect-size.md): mean delta and its 95% paired
# t-interval over the 20 per-record deltas, by judge config and mutation type.
@pytest.mark.parametrize(
    ("config", "mutation_type", "mean", "ci"),
    [
        ("default", "entity_swap", 0.088, (0.045, 0.130)),
        ("default", "verbosity_pad", -0.005, (-0.027, 0.017)),
        ("default", "reformat", -0.018, (-0.048, 0.013)),
        ("penalize", "entity_swap", 0.093, (0.023, 0.164)),
        ("penalize", "verbosity_pad", -0.015, (-0.052, 0.022)),
        ("penalize", "reformat", -0.008, (-0.040, 0.023)),
    ],
)
def test_spike_deltas_match_results_tables(
    spike_rate: Callable[[str, str], DetectionRate],
    config: str,
    mutation_type: str,
    mean: float,
    ci: tuple[float, float],
) -> None:
    result = paired_delta(spike_rate(config, mutation_type).outcomes)

    assert result.n == 20
    assert result.mean == pytest.approx(mean, abs=5e-4)
    assert result.ci == pytest.approx(ci, abs=5e-4)


def test_spike_effect_size_uses_pooled_noise_floor(
    spike_rate: Callable[[str, str], DetectionRate],
) -> None:
    # The spike reported 3.11 against its mean-of-SDs noise floor (0.028). Against the pooled
    # sigma that docs/metrics.md adopted (0.0536), the same 0.088 mean drop is 1.64 noise SDs.
    rate = spike_rate("default", "entity_swap")

    assert effect_size(paired_delta(rate.outcomes), rate.sigma) == pytest.approx(1.64, abs=0.01)


def test_known_deltas_give_known_mean_and_t_interval() -> None:
    result = paired_delta([outcome("a", 0.1), outcome("b", 0.2), outcome("c", 0.3)])

    # t(0.975, df=2) = 4.3027; half-width = 4.3027 * 0.1 / sqrt(3).
    half = 4.302653 * 0.1 / math.sqrt(3)
    assert result == PairedDelta(
        mean=pytest.approx(0.2),
        ci=pytest.approx((0.2 - half, 0.2 + half)),
        sd=pytest.approx(0.1),
        n=3,
    )


def test_counts_every_mutant_with_a_delta_and_skips_the_rest() -> None:
    outcomes = [
        outcome("changed", 0.3),
        outcome("no_headroom", 0.01, ExclusionReason.NO_HEADROOM),
        outcome("no_change", None, ExclusionReason.NO_CHANGE),
        outcome("no_scores", None, ExclusionReason.NO_SCORES),
    ]

    result = paired_delta(outcomes)

    assert result.n == 2
    assert result.mean == pytest.approx(0.155)


def test_no_deltas_gives_no_mean() -> None:
    assert paired_delta([]) == PairedDelta(None, None, None, 0)
    assert paired_delta([outcome("a", None, ExclusionReason.NO_CHANGE)]) == PairedDelta(
        None, None, None, 0
    )


def test_one_delta_has_a_mean_but_no_spread() -> None:
    assert paired_delta([outcome("a", 0.25)]) == PairedDelta(0.25, None, None, 1)


def test_identical_deltas_give_a_zero_width_interval() -> None:
    result = paired_delta([outcome("a", 0.2), outcome("b", 0.2)])

    assert result.sd == 0.0
    assert result.ci == pytest.approx((0.2, 0.2))


def test_level_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="level"):
        paired_delta([outcome("a", 0.1)], level=1.0)


def test_effect_size_is_mean_delta_in_noise_units() -> None:
    delta = PairedDelta(0.1, (0.05, 0.15), 0.05, 4)

    assert effect_size(delta, 0.05) == pytest.approx(2.0)
    assert effect_size(delta, 0.0) is None
    assert effect_size(PairedDelta(None, None, None, 0), 0.05) is None
    with pytest.raises(ValueError, match="sigma"):
        effect_size(delta, -0.1)


def test_end_to_end_from_detection_rate_outcomes() -> None:
    originals = {"a": [1.0, 0.9, 1.0], "b": [0.8, 0.9, 0.7], "c": [1.0, 1.0, 1.0]}
    mutants = [
        MutantScores("a", [0.8, 0.7, 0.6]),  # drop 0.2667
        MutantScores("b", [0.8, 0.8, 0.8]),  # drop 0.0
        MutantScores("c", [1.0, 1.0, 1.0], changed=False),  # skipped
    ]

    rate = detection_rate(originals, mutants)
    result = paired_delta(rate.outcomes)

    assert result.n == 2
    assert result.mean == pytest.approx((0.9666667 - 0.7) / 2)
    assert result.ci is not None and result.ci[0] < result.mean < result.ci[1]
