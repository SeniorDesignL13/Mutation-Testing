"""Spurious sensitivity: the headline metric for preserving operators.

A preserving mutant counts as flagged when the judge's mean score on it moves, in either
direction, by more than the judge's own run-to-run noise can explain. The noise floor is the
same one detection rate uses (``mtlj.stats.detection.noise_floor``), so both metrics measure a
judge against the same yardstick. A judge that ignores the change still gets flagged on about
alpha of mutants through noise alone, so the rate is read against alpha, not against 0.
docs/metrics.md ("Spurious sensitivity") defines the metric and explains the choices behind it;
this module implements it as written there.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from scipy.stats import binomtest

from mtlj.stats import detection
from mtlj.stats.detection import (
    DEFAULT_ALPHA,
    EPSILON,
    ExclusionReason,
    MutantOutcome,
    MutantScores,
    noise_floor,
    wilson_interval,
)

# An original near 0 can still rise and one near 1 can still fall, so no-headroom never applies.
EXCLUSION_REASONS = (ExclusionReason.NO_CHANGE, ExclusionReason.NO_SCORES)


@dataclass(frozen=True, slots=True)
class SpuriousSensitivity:
    """Spurious sensitivity for one judge, judge config and operator, with what's reported with it.

    ``rate``, ``ci`` and ``p_value`` are None when no mutant is testable. ``p_value`` is the
    one-sided exact binomial test of ``flagged`` against ``alpha``: the chance that a judge
    blind to the change gets at least this many flags. ``above_chance`` is True when it's below
    ``alpha``. ``tau`` is the threshold at ``n_samples`` scores on both sides. In ``outcomes``,
    ``detected`` means flagged, and the sign of ``delta`` says which way the score moved.
    """

    rate: float | None
    ci: tuple[float, float] | None
    p_value: float | None
    above_chance: bool
    flagged: int
    penalized: int
    rewarded: int
    testable: int
    excluded: dict[ExclusionReason, int]
    sigma: float
    tau: float
    n_samples: int
    alpha: float
    outcomes: tuple[MutantOutcome, ...]


def threshold(sigma: float, n_original: int, n_mutant: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Flag threshold tau: the move in mean score, either way, that noise exceeds with odds alpha.

    Two-sided, because a preserving operator predicts no move at all. Each side gets alpha / 2,
    so this is detection's one-sided threshold at half the alpha.
    """
    return detection.threshold(sigma, n_original, n_mutant, alpha / 2)


def flag_mutant(
    original: Sequence[float],
    mutant: MutantScores,
    sigma: float,
    alpha: float = DEFAULT_ALPHA,
) -> MutantOutcome:
    """Decide whether the judge's score moved on one preserving mutant, or why it can't be tested.

    Args:
        original: successful scores of the mutant's original.
        mutant: the mutant's successful scores.
        sigma: the run's noise floor (see ``detection.noise_floor``).
        alpha: two-sided false-flag rate.
    """
    if not mutant.changed:
        return MutantOutcome(mutant.record_id, False, ExclusionReason.NO_CHANGE, None, None)
    if not original or not mutant.scores:
        return MutantOutcome(mutant.record_id, False, ExclusionReason.NO_SCORES, None, None)

    tau = threshold(sigma, len(original), len(mutant.scores), alpha)
    delta = statistics.fmean(original) - statistics.fmean(mutant.scores)
    return MutantOutcome(mutant.record_id, abs(delta) > tau + EPSILON, None, delta, tau)


def spurious_sensitivity(
    originals: Mapping[str, Sequence[float]],
    mutants: Iterable[MutantScores],
    alpha: float = DEFAULT_ALPHA,
) -> SpuriousSensitivity:
    """Spurious sensitivity of one preserving operator's mutants, for one judge and judge config.

    Args:
        originals: successful scores of every original in the run, by record id. All of them
            feed the noise floor, including originals whose mutant is excluded. A record id
            that's missing here means its original has no successful scores.
        mutants: the operator's mutants.
        alpha: two-sided false-flag rate, also the rate the result is tested against.
            docs/metrics.md fixes it at 0.05.

    Returns:
        The rate with its 95% Wilson interval and the binomial test against alpha, the counts
        behind it, and sigma, tau, N and alpha. ``n_samples`` is the most scores any text
        received, as in ``detection.detection_rate``.

    Raises:
        ValueError: if no original has two or more scores (see ``noise_floor``).
    """
    mutants = tuple(mutants)
    sigma = noise_floor(originals.values())
    outcomes = tuple(flag_mutant(originals.get(m.record_id, ()), m, sigma, alpha) for m in mutants)

    reasons = Counter(o.excluded for o in outcomes)
    testable = reasons[None]
    flagged = [o for o in outcomes if o.detected]
    penalized = sum(o.delta is not None and o.delta > 0 for o in flagged)
    p_value = (
        float(binomtest(len(flagged), testable, alpha, alternative="greater").pvalue)
        if testable
        else None
    )
    n_samples = max(len(s) for s in [*originals.values(), *(m.scores for m in mutants)])

    return SpuriousSensitivity(
        rate=len(flagged) / testable if testable else None,
        ci=wilson_interval(len(flagged), testable) if testable else None,
        p_value=p_value,
        above_chance=p_value is not None and p_value < alpha,
        flagged=len(flagged),
        penalized=penalized,
        rewarded=len(flagged) - penalized,
        testable=testable,
        excluded={reason: reasons[reason] for reason in EXCLUSION_REASONS},
        sigma=sigma,
        tau=threshold(sigma, n_samples, n_samples, alpha),
        n_samples=n_samples,
        alpha=alpha,
        outcomes=outcomes,
    )
