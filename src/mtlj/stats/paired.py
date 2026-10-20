"""Paired original-versus-mutant comparison: how far an operator moves the score.

Detection rate (``mtlj.stats.detection``) says how many mutants dropped by more than noise. This
module says how far they dropped on average. Each mutant is compared with its own original, so
record-level variation (some answers are harder to judge than others) cancels out and the mean
drop can be estimated from a modest corpus. docs/metrics.md ("What detection rate doesn't tell
you") asks for the mean drop and effect size to be reported with every detection rate.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from scipy.stats import t as student_t

from mtlj.stats.detection import CI_LEVEL, MutantOutcome


@dataclass(frozen=True, slots=True)
class PairedDelta:
    """Mean paired drop in score across one operator's mutants, for one judge and judge config.

    ``mean`` is None when no mutant has a delta. ``sd`` and ``ci`` are None with fewer than two
    deltas, since the spread can't be estimated from one.
    """

    mean: float | None
    ci: tuple[float, float] | None
    sd: float | None
    n: int


def paired_delta(outcomes: Iterable[MutantOutcome], level: float = CI_LEVEL) -> PairedDelta:
    """Mean of the per-mutant paired deltas, with a Student-t interval for that mean.

    Every mutant with a delta counts, including those the detection rate excludes for lack of
    headroom: the question here is how far scores moved, not whether a drop was detectable.
    Mutants excluded before a delta could be computed (no change, no scores) are skipped.

    The interval is the paired t-interval over the per-mutant deltas, as the MTLJ-31 spike
    reported it. It's two-sided: a preserving operator is judged on whether the interval includes
    0, a degrading one on how far it sits above 0.
    """
    if not 0 < level < 1:
        raise ValueError(f"level must be in (0, 1), got {level}")
    deltas = [o.delta for o in outcomes if o.delta is not None]
    n = len(deltas)
    if n == 0:
        return PairedDelta(None, None, None, 0)
    mean = statistics.fmean(deltas)
    if n == 1:
        return PairedDelta(mean, None, None, 1)
    sd = statistics.stdev(deltas)
    half = float(student_t.ppf((1 + level) / 2, n - 1)) * sd / math.sqrt(n)
    return PairedDelta(mean, (mean - half, mean + half), sd, n)


def effect_size(delta: PairedDelta, sigma: float) -> float | None:
    """Mean paired delta in units of the judge's noise floor.

    ``sigma`` is the run's pooled noise floor (``detection.noise_floor``), so the result says how
    many noise SDs the operator moves the score. This is the spike's effect size with the pooled
    sigma in place of its mean of SDs. None when there's no mean, or when sigma is 0 (any drop is
    then infinitely large relative to noise, which isn't a useful number).
    """
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma}")
    if delta.mean is None or sigma == 0:
        return None
    return delta.mean / sigma
