"""Detection rate: the headline metric for degrading operators.

A degrading mutant counts as detected when the judge's mean score on it drops by more than the
judge's own run-to-run noise can explain. That noise is measured on the unmutated originals of
the same run, so the threshold adapts to each judge and judge config, and detection rates stay
comparable across them. docs/metrics.md ("Detection rate") defines the metric and explains the
choices behind it; this module implements it as written there.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from statistics import NormalDist

DEFAULT_ALPHA = 0.05
CI_LEVEL = 0.95


class ExclusionReason(Enum):
    """Why a mutant is left out of the detection rate's denominator."""

    NO_CHANGE = "no_change"  # the operator returned changed=False
    NO_SCORES = "no_scores"  # the original or the mutant has no successful score
    NO_HEADROOM = "no_headroom"  # mean(original) <= tau: no drop can be bigger than tau


@dataclass(frozen=True, slots=True)
class MutantScores:
    """One degrading mutant's successful judge scores, linked to its original by record id."""

    record_id: str
    scores: Sequence[float]
    changed: bool = True


@dataclass(frozen=True, slots=True)
class MutantOutcome:
    """The verdict on one mutant.

    ``delta`` and ``tau`` are None for a mutant excluded before they could be computed (no
    change, no scores).
    """

    record_id: str
    detected: bool
    excluded: ExclusionReason | None
    delta: float | None
    tau: float | None


@dataclass(frozen=True, slots=True)
class DetectionRate:
    """Detection rate for one judge, judge config and operator, with what's reported with it.

    ``rate`` and ``ci`` are None when no mutant is testable, since 0 of 0 isn't a rate.
    ``tau`` is the threshold at ``n_samples`` scores on both sides. A mutant or original with
    fewer successful scores gets its own, larger threshold, recorded in its outcome.
    """

    rate: float | None
    ci: tuple[float, float] | None
    detected: int
    testable: int
    excluded: dict[ExclusionReason, int]
    sigma: float
    tau: float
    n_samples: int
    alpha: float
    outcomes: tuple[MutantOutcome, ...]


def noise_floor(originals: Iterable[Sequence[float]]) -> float:
    """Pooled within-original SD: how far the judge's score moves when nothing changed.

    Each original's sample variance is weighted by its degrees of freedom (its number of scores
    minus one). Originals with fewer than two scores say nothing about noise and are skipped.

    Raises:
        ValueError: if no original has two or more scores, so noise can't be measured.
    """
    pooled = [s for s in originals if len(s) >= 2]
    df = sum(len(s) - 1 for s in pooled)
    if df == 0:
        raise ValueError("the noise floor needs at least one original with two or more scores")
    return math.sqrt(sum((len(s) - 1) * statistics.variance(s) for s in pooled) / df)


def threshold(sigma: float, n_original: int, n_mutant: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Detection threshold tau: the drop in mean score that noise alone exceeds with odds alpha.

    One-sided, because a degrading operator predicts a drop.
    """
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma}")
    if n_original < 1 or n_mutant < 1:
        raise ValueError(f"need at least one score per side, got {n_original} and {n_mutant}")
    if not 0 < alpha < 0.5:
        raise ValueError(f"alpha must be in (0, 0.5), got {alpha}")
    if sigma == 0:
        # A fully deterministic judge has no noise to clear, so any strict drop counts.
        return 0.0
    return NormalDist().inv_cdf(1 - alpha) * sigma * math.sqrt(1 / n_original + 1 / n_mutant)


def wilson_interval(k: int, n: int, level: float = CI_LEVEL) -> tuple[float, float]:
    """Wilson score interval for the proportion k/n.

    Used instead of the normal-approximation interval because it behaves at 0% and 100% and
    with few mutants.
    """
    if n < 1 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n and n >= 1, got k={k}, n={n}")
    z = NormalDist().inv_cdf(1 - (1 - level) / 2)
    p = k / n
    scale = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / scale
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / scale
    # At 0% and 100% the bound is exactly 0 or 1; the formula only gets there up to rounding.
    low = 0.0 if k == 0 else centre - half
    high = 1.0 if k == n else centre + half
    return low, high


def detect_mutant(
    original: Sequence[float],
    mutant: MutantScores,
    sigma: float,
    alpha: float = DEFAULT_ALPHA,
) -> MutantOutcome:
    """Decide whether one mutant was detected, or why it can't be tested.

    Args:
        original: successful scores of the mutant's original.
        mutant: the mutant's successful scores.
        sigma: the run's noise floor (see ``noise_floor``).
        alpha: one-sided false-detection rate.
    """
    if not mutant.changed:
        return MutantOutcome(mutant.record_id, False, ExclusionReason.NO_CHANGE, None, None)
    if not original or not mutant.scores:
        return MutantOutcome(mutant.record_id, False, ExclusionReason.NO_SCORES, None, None)

    tau = threshold(sigma, len(original), len(mutant.scores), alpha)
    original_mean = statistics.fmean(original)
    delta = original_mean - statistics.fmean(mutant.scores)
    if original_mean <= tau:
        return MutantOutcome(mutant.record_id, False, ExclusionReason.NO_HEADROOM, delta, tau)
    return MutantOutcome(mutant.record_id, delta > tau, None, delta, tau)


def detection_rate(
    originals: Mapping[str, Sequence[float]],
    mutants: Iterable[MutantScores],
    alpha: float = DEFAULT_ALPHA,
) -> DetectionRate:
    """Detection rate of one degrading operator's mutants, for one judge and judge config.

    Args:
        originals: successful scores of every original in the run, by record id. All of them
            feed the noise floor, including originals whose mutant is excluded. A record id
            that's missing here means its original has no successful scores.
        mutants: the operator's mutants.
        alpha: one-sided false-detection rate. docs/metrics.md fixes it at 0.05.

    Returns:
        The rate with its 95% Wilson interval, the counts behind it, and sigma, tau, N and
        alpha. ``n_samples`` is the most scores any text received: the N the run asked for,
        since failed scorings only lower a text's count.

    Raises:
        ValueError: if no original has two or more scores (see ``noise_floor``).
    """
    mutants = tuple(mutants)
    sigma = noise_floor(originals.values())
    outcomes = tuple(
        detect_mutant(originals.get(m.record_id, ()), m, sigma, alpha) for m in mutants
    )

    reasons = Counter(o.excluded for o in outcomes)
    testable = reasons[None]
    detected = sum(o.detected for o in outcomes)
    n_samples = max(len(s) for s in [*originals.values(), *(m.scores for m in mutants)])

    return DetectionRate(
        rate=detected / testable if testable else None,
        ci=wilson_interval(detected, testable) if testable else None,
        detected=detected,
        testable=testable,
        excluded={reason: reasons[reason] for reason in ExclusionReason},
        sigma=sigma,
        tau=threshold(sigma, n_samples, n_samples, alpha),
        n_samples=n_samples,
        alpha=alpha,
        outcomes=outcomes,
    )
