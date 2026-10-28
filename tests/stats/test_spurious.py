"""Tests for the spurious sensitivity defined in docs/metrics.md (MTLJ-33)."""

from collections.abc import Callable

import pytest

from mtlj.stats import detection
from mtlj.stats.detection import ExclusionReason, MutantOutcome, MutantScores, noise_floor
from mtlj.stats.paired import paired_delta
from mtlj.stats.spurious import SpuriousSensitivity, spurious_sensitivity, threshold

# What the spike_texts fixture returns: originals by record id, and one type's mutants.
SpikeTexts = tuple[dict[str, list[float]], list[MutantScores]]

# The worked example's table in docs/metrics.md (default config, reformat):
# record -> (delta, flagged).
WORKED_EXAMPLE = {
    "13122": (0.067, True),
    "15450": (0.000, False),
    "16980": (0.033, False),
    "17622": (0.000, False),
    "13302": (0.000, False),
    "17388": (0.056, False),
    "13560": (0.000, False),
    "15204": (-0.143, True),
    "14922": (-0.114, True),
    "12660": (0.000, False),
    "17148": (0.000, False),
    "12852": (-0.029, False),
    "17490": (0.054, False),
    "17028": (0.000, False),
    "11868": (0.000, False),
    "17592": (-0.025, False),
    "14352": (-0.067, True),
    "17472": (-0.190, True),
    "12018": (0.000, False),
    "16080": (0.000, False),
}


def outcome_of(result: SpuriousSensitivity, record_id: str) -> MutantOutcome:
    return next(o for o in result.outcomes if o.record_id == record_id)


@pytest.mark.parametrize(
    ("config", "mutation_type", "tau", "penalized", "rewarded", "ci_percent", "p_value"),
    [
        ("default", "verbosity_pad", 0.0665, 1, 2, (5, 36), 0.0755),
        ("default", "reformat", 0.0665, 1, 4, (11, 47), 0.0026),
        ("penalize", "verbosity_pad", 0.0767, 2, 3, (11, 47), 0.0026),
        ("penalize", "reformat", 0.0767, 3, 2, (11, 47), 0.0026),
    ],
)
def test_spike_preserving_mutants_match_worked_example(
    spike_texts: Callable[[str, str], SpikeTexts],
    config: str,
    mutation_type: str,
    tau: float,
    penalized: int,
    rewarded: int,
    ci_percent: tuple[int, int],
    p_value: float,
) -> None:
    result = spurious_sensitivity(*spike_texts(config, mutation_type))
    flagged = penalized + rewarded

    assert round(result.tau, 4) == tau
    assert (result.penalized, result.rewarded) == (penalized, rewarded)
    assert (result.flagged, result.testable) == (flagged, 20)
    assert result.rate == flagged / 20
    assert result.ci is not None
    assert (round(result.ci[0] * 100), round(result.ci[1] * 100)) == ci_percent
    assert round(result.p_value or 0, 4) == p_value
    assert result.above_chance is (p_value < 0.05)
    assert result.excluded == dict.fromkeys(
        [ExclusionReason.NO_CHANGE, ExclusionReason.NO_SCORES], 0
    )
    assert (result.n_samples, result.alpha) == (5, 0.05)


def test_spike_reformat_verdicts_match_worked_example_table(
    spike_texts: Callable[[str, str], SpikeTexts],
) -> None:
    result = spurious_sensitivity(*spike_texts("default", "reformat"))

    for record_id, (delta, flagged) in WORKED_EXAMPLE.items():
        outcome = outcome_of(result, record_id)
        assert outcome.delta == pytest.approx(delta, abs=5e-4), record_id
        assert outcome.detected is flagged, record_id


def test_spike_reformat_moves_cancel_in_the_mean(
    spike_texts: Callable[[str, str], SpikeTexts],
) -> None:
    # Above chance by count, yet the paired mean delta's interval includes 0.
    result = spurious_sensitivity(*spike_texts("default", "reformat"))
    delta = paired_delta(result.outcomes)

    assert result.above_chance
    assert delta.ci is not None and delta.ci[0] < 0 < delta.ci[1]


def test_spike_noise_floor_is_detection_rates(
    spike_texts: Callable[[str, str], SpikeTexts],
) -> None:
    originals, mutants = spike_texts("default", "reformat")

    assert spurious_sensitivity(originals, mutants).sigma == noise_floor(originals.values())


@pytest.mark.parametrize(("n", "ratio"), [(3, 1.60), (5, 1.24), (10, 0.88)])
def test_threshold_is_two_sided(n: int, ratio: float) -> None:
    assert threshold(0.05, n, n) == detection.threshold(0.05, n, n, alpha=0.025)
    assert round(threshold(1.0, n, n), 2) == ratio


def test_flags_moves_in_both_directions() -> None:
    originals = {"down": [0.8, 0.9, 0.85], "up": [0.8, 0.9, 0.85], "flat": [0.8, 0.9, 0.85]}
    mutants = [
        MutantScores("down", [0.5] * 3),
        MutantScores("up", [1.0] * 3),
        MutantScores("flat", [0.84, 0.86, 0.85]),
    ]

    result = spurious_sensitivity(originals, mutants)

    assert [o.detected for o in result.outcomes] == [True, True, False]
    assert (result.penalized, result.rewarded, result.flagged) == (1, 1, 2)


def test_zero_noise_flags_any_real_move_but_not_rounding() -> None:
    originals = {"a": [0.8] * 3, "b": [0.8] * 3, "c": [0.8] * 3}
    mutants = [
        MutantScores("a", [0.75] * 3),
        MutantScores("b", [0.85] * 3),
        # 0.7, 0.9 and 0.8 average to 0.8, but in floating point delta comes out just above 0.
        MutantScores("c", [0.7, 0.9, 0.8]),
    ]

    result = spurious_sensitivity(originals, mutants)

    assert (result.sigma, result.tau) == (0.0, 0.0)
    assert [o.detected for o in result.outcomes] == [True, True, False]


def test_original_at_a_bound_stays_testable() -> None:
    # Detection rate excludes an original at 0 for lack of headroom; here it can still rise.
    originals = {"floor": [0.0, 0.0, 0.1], "ceiling": [1.0, 1.0, 0.9]}
    mutants = [MutantScores("floor", [0.5] * 3), MutantScores("ceiling", [0.5] * 3)]

    result = spurious_sensitivity(originals, mutants)

    assert (result.penalized, result.rewarded, result.testable) == (1, 1, 2)


def test_p_value_is_the_binomial_tail_against_alpha() -> None:
    # Of 20 testable mutants, 3 flags happen by chance 7.5% of the time, 4 flags 1.6%.
    originals = {str(i): [1.0, 0.9] for i in range(20)}

    def run(flags: int) -> SpuriousSensitivity:
        mutants = [MutantScores(str(i), [0.0, 0.0] if i < flags else [0.95]) for i in range(20)]
        return spurious_sensitivity(originals, mutants)

    assert run(0).p_value == pytest.approx(1.0)
    assert (run(3).p_value, run(3).above_chance) == (pytest.approx(0.0755, abs=1e-4), False)
    assert (run(4).p_value, run(4).above_chance) == (pytest.approx(0.0159, abs=1e-4), True)


def test_excluded_mutants_are_counted_and_leave_no_rate() -> None:
    originals = {"a": [1.0, 0.9], "b": [1.0, 0.9]}
    mutants = [
        MutantScores("a", [], changed=False),
        MutantScores("b", []),
        MutantScores("missing", [0.5, 0.5]),
    ]

    result = spurious_sensitivity(originals, mutants)

    assert (result.rate, result.ci, result.p_value) == (None, None, None)
    assert not result.above_chance
    assert (result.flagged, result.testable) == (0, 0)
    assert result.excluded == {ExclusionReason.NO_CHANGE: 1, ExclusionReason.NO_SCORES: 2}


def test_needs_an_original_with_two_scores() -> None:
    with pytest.raises(ValueError, match="two or more scores"):
        spurious_sensitivity({"a": [0.9]}, [MutantScores("a", [0.9])])
