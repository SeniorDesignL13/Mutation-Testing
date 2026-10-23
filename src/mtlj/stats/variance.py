"""Per-condition score variance: how noisy the judge is on each kind of text in a run.

Every claim mtlj makes is measured in units of the judge's run-to-run noise: detection rate's
threshold and the effect size are both multiples of sigma. Those metrics take sigma from the
unmutated originals and assume a mutant's scores scatter the same way. This module estimates the
variance for the originals (the noise baseline) and separately for each operator and intensity,
so that assumption can be checked instead of assumed, and stores the estimates with the run.

The estimator is the one ``detection.noise_floor`` uses: each text's sample variance over its
scores, pooled across texts and weighted by degrees of freedom (score count minus one). It's
unbiased whatever the score distribution. The confidence intervals assume normally distributed
scores, which coarse judge scores only approximate, so treat them as a guide.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scipy.stats import chi2
from scipy.stats import f as f_dist

from mtlj.stats.detection import CI_LEVEL

# The file a run's variance report is saved as, in the run's directory.
VARIANCE_FILE = "variance.json"


@dataclass(frozen=True, slots=True, order=True)
class Condition:
    """One operator at one intensity: the mutants a run scores together."""

    operator: str
    intensity: float


@dataclass(frozen=True, slots=True)
class VarianceEstimate:
    """Pooled within-text score variance of a set of texts, with its confidence interval.

    ``df`` is the degrees of freedom behind it (each text's score count minus one, summed), and
    ``n_texts`` the number of texts with two or more scores, the only ones that show noise.
    """

    variance: float
    sd: float
    df: int
    n_texts: int
    ci: tuple[float, float]


@dataclass(frozen=True, slots=True)
class ConditionVariance:
    """One condition's variance, and how it compares with the originals' baseline.

    ``ratio`` is the condition's variance over the baseline's: above 1, the judge is noisier on
    these mutants than on the originals. If ``ratio_ci`` excludes 1, the metrics' assumption that
    mutants scatter like originals doesn't hold for this condition. ``estimate`` is None when no
    mutant has two or more scores; ``ratio`` and ``ratio_ci`` are None then too, and when the
    baseline variance is 0.
    """

    condition: Condition
    estimate: VarianceEstimate | None
    ratio: float | None
    ratio_ci: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class VarianceReport:
    """Score variance of one judge and judge config in a run: the baseline and every condition."""

    baseline: VarianceEstimate
    conditions: tuple[ConditionVariance, ...]
    level: float

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable form of the report, for storing with the run."""
        return {
            "level": self.level,
            "baseline": _estimate_to_dict(self.baseline),
            "conditions": [
                {
                    "operator": c.condition.operator,
                    "intensity": c.condition.intensity,
                    "estimate": _estimate_to_dict(c.estimate),
                    "ratio": c.ratio,
                    "ratio_ci": c.ratio_ci,
                }
                for c in self.conditions
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VarianceReport:
        """Rebuild a report from ``to_dict``'s output."""
        baseline = _estimate_from_dict(data["baseline"])
        if baseline is None:
            raise ValueError("a variance report needs a baseline")
        return cls(
            baseline=baseline,
            conditions=tuple(
                ConditionVariance(
                    condition=Condition(c["operator"], c["intensity"]),
                    estimate=_estimate_from_dict(c["estimate"]),
                    ratio=c["ratio"],
                    ratio_ci=None if c["ratio_ci"] is None else _pair(c["ratio_ci"]),
                )
                for c in data["conditions"]
            ),
            level=data["level"],
        )


def pooled_variance(
    texts: Iterable[Sequence[float]], level: float = CI_LEVEL
) -> VarianceEstimate | None:
    """Pooled within-text sample variance of a set of texts, with its chi-square interval.

    Each text's sample variance is weighted by its degrees of freedom (its score count minus
    one), as in ``detection.noise_floor``. Texts with fewer than two scores say nothing about
    noise and are skipped. None if no text has two or more scores.
    """
    if not 0 < level < 1:
        raise ValueError(f"level must be in (0, 1), got {level}")
    pooled = [s for s in texts if len(s) >= 2]
    df = sum(len(s) - 1 for s in pooled)
    if df == 0:
        return None
    # statistics.variance is exact, so a text scored the same every time contributes exactly 0.
    sum_of_squares = math.fsum((len(s) - 1) * statistics.variance(s) for s in pooled)
    variance = sum_of_squares / df
    tail = (1 - level) / 2
    ci = (
        float(sum_of_squares / chi2.ppf(1 - tail, df)),
        float(sum_of_squares / chi2.ppf(tail, df)),
    )
    return VarianceEstimate(variance, math.sqrt(variance), df, len(pooled), ci)


def compare_to_baseline(
    condition: Condition,
    estimate: VarianceEstimate | None,
    baseline: VarianceEstimate,
    level: float = CI_LEVEL,
) -> ConditionVariance:
    """A condition's variance as a ratio to the baseline's, with an F interval for that ratio.

    The interval treats the two estimates as independent, which they are: they come from
    different texts, each scored separately.
    """
    if estimate is None or baseline.variance == 0:
        return ConditionVariance(condition, estimate, None, None)
    ratio = estimate.variance / baseline.variance
    tail = (1 - level) / 2
    ratio_ci = (
        float(ratio / f_dist.ppf(1 - tail, estimate.df, baseline.df)),
        float(ratio / f_dist.ppf(tail, estimate.df, baseline.df)),
    )
    return ConditionVariance(condition, estimate, ratio, ratio_ci)


def condition_variances(
    originals: Mapping[str, Sequence[float]],
    mutants: Mapping[Condition, Mapping[str, Sequence[float]]],
    level: float = CI_LEVEL,
) -> VarianceReport:
    """Score variance of the originals and of every operator-and-intensity condition in a run.

    Args:
        originals: successful scores of every original in the run, by record id. Their variance
            is the noise baseline; its square root is ``detection.noise_floor``.
        mutants: successful scores of every mutant, by condition and then by record id.
        level: confidence level of the intervals.

    Returns:
        The baseline, and each condition's estimate and ratio to it, sorted by operator and
        then intensity.

    Raises:
        ValueError: if no original has two or more scores, so there's no baseline.
    """
    baseline = pooled_variance(originals.values(), level)
    if baseline is None:
        raise ValueError("the noise baseline needs at least one original with two or more scores")
    conditions = tuple(
        compare_to_baseline(c, pooled_variance(mutants[c].values(), level), baseline, level)
        for c in sorted(mutants)
    )
    return VarianceReport(baseline, conditions, level)


def save_report(report: VarianceReport, run_dir: Path) -> Path:
    """Store the report with the run, as ``variance.json`` in the run's directory.

    A run that scores several judges or judge configs gives each its own directory.
    """
    path = run_dir / VARIANCE_FILE
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_report(run_dir: Path) -> VarianceReport:
    """Read back the report ``save_report`` stored in a run's directory."""
    return VarianceReport.from_dict(
        json.loads((run_dir / VARIANCE_FILE).read_text(encoding="utf-8"))
    )


def _estimate_to_dict(estimate: VarianceEstimate | None) -> dict[str, Any] | None:
    if estimate is None:
        return None
    return {
        "variance": estimate.variance,
        "sd": estimate.sd,
        "df": estimate.df,
        "n_texts": estimate.n_texts,
        "ci": estimate.ci,
    }


def _estimate_from_dict(data: Mapping[str, Any] | None) -> VarianceEstimate | None:
    if data is None:
        return None
    return VarianceEstimate(
        variance=data["variance"],
        sd=data["sd"],
        df=data["df"],
        n_texts=data["n_texts"],
        ci=_pair(data["ci"]),
    )


def _pair(values: Sequence[float]) -> tuple[float, float]:
    low, high = values
    return low, high
