"""Tests for per-condition score variance (MTLJ-38).

The synthetic tests draw scores with a known variance and check that the estimates recover it,
and that the intervals cover it at their stated level.
"""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from mtlj.stats.detection import noise_floor
from mtlj.stats.variance import (
    VARIANCE_FILE,
    Condition,
    VarianceReport,
    condition_variances,
    load_report,
    pooled_variance,
    save_report,
)

SPIKE_SCORES = Path(__file__).parents[2] / "spike" / "scores.csv"
SEED = 38
REPS = 1000


def synthetic_texts(
    rng: np.random.Generator, sd: float, n_texts: int, n_scores: int
) -> dict[str, list[float]]:
    """Scores of ``n_texts`` texts, each with its own mean and run-to-run noise ``sd``."""
    means = rng.uniform(0.3, 0.7, size=n_texts)
    scores = rng.normal(means[:, None], sd, size=(n_texts, n_scores))
    return {str(i): row.tolist() for i, row in enumerate(scores)}


def test_pooled_variance_weights_texts_by_degrees_of_freedom() -> None:
    # Variances 0.125 (df 1) and 0.04 (df 2); the single score carries no df and is skipped.
    estimate = pooled_variance([[1.0, 0.5], [0.2, 0.4, 0.6], [0.3]])

    assert estimate is not None
    assert estimate.variance == pytest.approx((0.125 + 2 * 0.04) / 3)
    assert estimate.sd == pytest.approx(math.sqrt(estimate.variance))
    assert (estimate.df, estimate.n_texts) == (3, 2)


def test_interval_is_the_chi_square_interval() -> None:
    # df 4 and variance 0.01: [0.04 / 11.1433, 0.04 / 0.4844].
    estimate = pooled_variance([[0.4, 0.5, 0.6], [0.4, 0.5, 0.6]])

    assert estimate is not None
    assert estimate.variance == pytest.approx(0.01)
    assert estimate.ci == pytest.approx((0.04 / 11.14329, 0.04 / 0.484419), rel=1e-5)


def test_texts_without_two_scores_give_no_estimate() -> None:
    assert pooled_variance([[0.9], []]) is None
    with pytest.raises(ValueError, match="two or more scores"):
        condition_variances({"a": [0.9]}, {})


def test_level_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="level"):
        pooled_variance([[0.1, 0.2]], level=1.0)


def test_baseline_is_the_squared_noise_floor() -> None:
    originals = synthetic_texts(np.random.default_rng(SEED), 0.05, 20, 5)

    report = condition_variances(originals, {})

    assert report.baseline.sd == pytest.approx(noise_floor(originals.values()))


def test_each_condition_gets_its_own_estimate_and_ratio() -> None:
    originals = {"a": [0.4, 0.5, 0.6], "b": [0.5, 0.5, 0.5]}  # variance 0.005, df 4
    steady = Condition("reformat", 1.0)
    noisy = Condition("padding", 0.5)
    unscored = Condition("padding", 1.0)
    mutants = {
        steady: {"a": [0.5, 0.5, 0.5], "b": [0.7, 0.7, 0.7]},
        noisy: {"a": [0.2, 0.5, 0.8], "b": [0.5, 0.5, 0.5]},
        unscored: {"a": [0.5], "b": []},
    }

    report = condition_variances(originals, mutants)
    by_condition = {c.condition: c for c in report.conditions}

    assert [c.condition for c in report.conditions] == [noisy, unscored, steady]
    assert by_condition[noisy].ratio == pytest.approx(9.0)
    assert by_condition[steady].ratio == 0.0
    assert by_condition[steady].ratio_ci == (0.0, 0.0)
    assert by_condition[unscored].estimate is None
    assert by_condition[unscored].ratio is None


def test_zero_baseline_variance_gives_no_ratio() -> None:
    report = condition_variances(
        {"a": [1.0, 1.0], "b": [0.8, 0.8]}, {Condition("padding", 1.0): {"a": [0.9, 1.0]}}
    )

    assert report.baseline.variance == 0.0
    assert report.conditions[0].estimate is not None
    assert (report.conditions[0].ratio, report.conditions[0].ratio_ci) == (None, None)


def test_recovers_known_variance_for_each_condition() -> None:
    # A large synthetic run with a different known SD per condition, and some failed scorings,
    # so texts have 3 to 5 scores.
    rng = np.random.default_rng(SEED)
    true_sd = {
        Condition("entity_swap", 1.0): 0.02,
        Condition("padding", 0.5): 0.05,
        Condition("padding", 1.0): 0.10,
    }
    originals = synthetic_texts(rng, 0.05, 500, 5)
    mutants = {c: synthetic_texts(rng, sd, 500, 5) for c, sd in true_sd.items()}
    for texts in [originals, *mutants.values()]:
        for scores in texts.values():
            del scores[rng.integers(3, 6) :]

    report = condition_variances(originals, mutants)

    # About 1,500 df each, so a relative standard error near 4%; allow 4 of them.
    assert report.baseline.variance == pytest.approx(0.05**2, rel=0.15)
    for result in report.conditions:
        true_variance = true_sd[result.condition] ** 2
        assert result.estimate is not None
        assert result.estimate.variance == pytest.approx(true_variance, rel=0.15)
        assert result.ratio == pytest.approx(true_variance / 0.05**2, rel=0.2)


def test_unbiased_for_coarse_scores() -> None:
    # Judge scores come in steps (about 1/8 for faithfulness). The estimate doesn't need normal
    # scores to be right on average.
    rng = np.random.default_rng(SEED)
    values, weights = np.array([0.75, 0.875, 1.0]), np.array([0.1, 0.3, 0.6])
    mean = values @ weights
    true_variance = ((values - mean) ** 2) @ weights

    estimates = []
    for _ in range(REPS):
        texts = rng.choice(values, p=weights, size=(20, 5)).tolist()
        estimate = pooled_variance(texts)
        assert estimate is not None
        estimates.append(estimate.variance)

    standard_error = np.std(estimates) / math.sqrt(REPS)
    assert abs(np.mean(estimates) - true_variance) < 3 * standard_error


def test_interval_covers_known_variance_at_its_level() -> None:
    rng = np.random.default_rng(SEED)

    covered = 0
    for _ in range(REPS):
        estimate = pooled_variance(synthetic_texts(rng, 0.05, 20, 5).values())
        assert estimate is not None
        covered += estimate.ci[0] <= 0.05**2 <= estimate.ci[1]

    # 3 Monte Carlo standard errors (0.007 each) either side of 95%.
    assert 0.93 < covered / REPS < 0.97


def test_ratio_interval_covers_known_ratio_at_its_level() -> None:
    rng = np.random.default_rng(SEED)
    condition = Condition("padding", 1.0)

    covered = 0
    for _ in range(REPS):
        originals = synthetic_texts(rng, 0.05, 20, 5)
        mutants = {condition: synthetic_texts(rng, 0.05 * math.sqrt(2), 20, 5)}
        ratio_ci = condition_variances(originals, mutants).conditions[0].ratio_ci
        assert ratio_ci is not None
        covered += ratio_ci[0] <= 2.0 <= ratio_ci[1]

    assert 0.93 < covered / REPS < 0.97


def test_report_is_stored_with_the_run_and_read_back(tmp_path: Path) -> None:
    rng = np.random.default_rng(SEED)
    mutants = {
        Condition("padding", 0.5): synthetic_texts(rng, 0.08, 10, 5),
        Condition("padding", 1.0): {"a": [0.5]},
    }
    report = condition_variances(synthetic_texts(rng, 0.05, 10, 5), mutants)

    path = save_report(report, tmp_path)

    assert path == tmp_path / VARIANCE_FILE
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert [c["intensity"] for c in stored["conditions"]] == [0.5, 1.0]
    assert stored["conditions"][1]["estimate"] is None
    assert load_report(tmp_path) == report


def test_report_without_a_baseline_is_rejected() -> None:
    with pytest.raises(ValueError, match="baseline"):
        VarianceReport.from_dict({"level": 0.95, "baseline": None, "conditions": []})


def test_spike_reformatted_answers_are_steadier_than_originals() -> None:
    # Default config of the MTLJ-31 spike: reformatted answers often scored 1.000 on every run,
    # so their variance is under half the originals', and the interval excludes 1.
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    with SPIKE_SCORES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["config"] == "default":
                scores[row["mutation_type"]][row["record_id"]].append(float(row["score"]))
    originals = scores.pop("original")

    report = condition_variances(originals, {Condition(t, 1.0): s for t, s in scores.items()})
    ratios = {c.condition.operator: c for c in report.conditions}

    assert report.baseline.sd == pytest.approx(0.0536, abs=5e-5)
    assert ratios["reformat"].ratio == pytest.approx(0.445, abs=5e-4)
    assert ratios["reformat"].ratio_ci is not None and ratios["reformat"].ratio_ci[1] < 1
    for operator in ("entity_swap", "verbosity_pad"):
        ratio_ci = ratios[operator].ratio_ci
        assert ratio_ci is not None and ratio_ci[0] < 1 < ratio_ci[1]
