"""Bootstrap confidence intervals for the per-mutant rates: detection rate and spurious sensitivity.

Each rate also carries a Wilson interval, which treats every mutant's verdict as a fixed coin
flip. That leaves one source of uncertainty out. The threshold tau is a multiple of the noise
floor sigma, and sigma is estimated from the same run's originals. A different sample of records
would give a different sigma, a different tau and so different verdicts, most of all for mutants
that clear tau by a hair. The bootstrap here resamples whole records and recomputes sigma, tau and
every verdict in each resample, so its interval covers both sources.

docs/metrics.md ("Bootstrap intervals") explains the choices. In short:

* **The record is the unit resampled.** A record carries its original's scores and the scores of
  its mutants, so a resample keeps each mutant paired with its own original. Every original in
  the run is a record, including those whose mutant is excluded or missing, because all of them
  feed sigma. The N scores within a text aren't resampled again: with data in levels, resampling
  only the top level is the standard choice, and resampling both overstates the spread.
* **Percentile interval** over ``DEFAULT_RESAMPLES`` resamples, from a fixed seed so a run's
  intervals can be reproduced exactly.

This module knows nothing about either metric's rule. Each metric module passes a ``Verdict``
function, the vectorized form of its own per-mutant rule, kept next to the scalar one there.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from mtlj.stats.detection import MutantScores

DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 0
# Resamples drawn at a time. Each batch holds a few (batch x mutants) arrays, so this bounds memory
# on a full corpus without slowing small runs.
_BATCH = 1_000

Floats = NDArray[np.float64]
Flags = NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    """A percentile bootstrap interval for a rate, with what's needed to reproduce it.

    ``n_valid`` counts the resamples in which the rate is defined. A resample with no testable
    mutant, or with no original that has two or more scores (so no sigma), has no rate and is
    left out. ``ci`` is None when no resample is valid.
    """

    ci: tuple[float, float] | None
    level: float
    n_resamples: int
    n_valid: int
    seed: int


@dataclass(frozen=True, slots=True)
class RecordSummaries:
    """What a resample needs from each record and each mutant, computed once up front.

    Per record (``ss``, ``df``): the original's sum of squared deviations from its mean, and its
    score count minus one. Both are 0 for an original with fewer than two scores or none, which
    then says nothing about noise, as in ``detection.noise_floor``.

    Per mutant: the index of its record, whether it has a delta at all (changed, and both it and
    its original have scores), sqrt(1/N_o + 1/N_m) (tau's dependence on the score counts), its
    original's mean score and its delta. The last three are 0 for a mutant without a delta.
    """

    ss: Floats
    df: Floats
    record: NDArray[np.intp]
    scored: Flags
    spread: Floats
    original_mean: Floats
    delta: Floats


# Given each resample's sigma (shape (B,)) and the summaries, the rule's verdict on every mutant
# in every resample, as (hits, testable), each of shape (B, mutants). A mutant counts towards the
# rate's numerator where both are True and towards its denominator where testable is.
Verdict = Callable[[Floats, RecordSummaries], tuple[Flags, Flags]]


def summarize(
    originals: Mapping[str, Sequence[float]], mutants: Iterable[MutantScores]
) -> RecordSummaries:
    """Summarize a run's records and one operator's mutants for resampling.

    Records are the originals in their mapping order, then any record id that only a mutant
    names (its original has no scores) in mutant order.
    """
    mutants = tuple(mutants)
    index = {record_id: i for i, record_id in enumerate(originals)}
    for m in mutants:
        index.setdefault(m.record_id, len(index))

    ss = np.zeros(len(index))
    df = np.zeros(len(index))
    for record_id, scores in originals.items():
        if len(scores) >= 2:
            df[index[record_id]] = len(scores) - 1
            ss[index[record_id]] = (len(scores) - 1) * statistics.variance(scores)

    scored = np.zeros(len(mutants), dtype=bool)
    spread = np.zeros(len(mutants))
    original_mean = np.zeros(len(mutants))
    delta = np.zeros(len(mutants))
    for i, m in enumerate(mutants):
        original = originals.get(m.record_id, ())
        if m.changed and original and m.scores:
            scored[i] = True
            spread[i] = math.sqrt(1 / len(original) + 1 / len(m.scores))
            original_mean[i] = statistics.fmean(original)
            delta[i] = original_mean[i] - statistics.fmean(m.scores)

    record = np.array([index[m.record_id] for m in mutants], dtype=np.intp)
    return RecordSummaries(ss, df, record, scored, spread, original_mean, delta)


def resampled_rates(summaries: RecordSummaries, counts: Floats, verdict: Verdict) -> Floats:
    """The rate in each resample, NaN where it's undefined.

    Args:
        summaries: the run's records and mutants (see ``summarize``).
        counts: shape (B, records): how many times each resample drew each record. A row of
            ones is the original sample, and gives the rate the metric itself reports.
        verdict: the metric's per-mutant rule.
    """
    pooled_df = counts @ summaries.df
    pooled_ss = counts @ summaries.ss
    with np.errstate(invalid="ignore", divide="ignore"):
        sigma = np.sqrt(np.where(pooled_df > 0, pooled_ss / pooled_df, np.nan))
    hits, testable = verdict(sigma, summaries)
    weights = counts[:, summaries.record]
    numerator = (weights * (hits & testable)).sum(axis=1)
    denominator = (weights * testable).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        rates = np.where(denominator > 0, numerator / denominator, np.nan)
    return np.where(np.isnan(sigma), np.nan, rates)


def percentile_interval(values: Floats, level: float) -> tuple[float, float]:
    """Central ``level`` interval of ``values``: its (1 - level)/2 and (1 + level)/2 quantiles."""
    tail = (1 - level) / 2
    low, high = np.quantile(values, [tail, 1 - tail])
    return float(low), float(high)


def bootstrap_rate(
    summaries: RecordSummaries,
    verdict: Verdict,
    level: float,
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> BootstrapInterval:
    """Percentile bootstrap interval for a rate, resampling whole records with replacement.

    Args:
        summaries: the run's records and one operator's mutants (see ``summarize``).
        verdict: the metric's per-mutant rule (see ``Verdict``).
        level: confidence level, in (0, 1).
        n_resamples: how many resamples to draw.
        seed: seeds the generator, so the same inputs always give the same interval.
    """
    if not 0 < level < 1:
        raise ValueError(f"level must be in (0, 1), got {level}")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be >= 1, got {n_resamples}")

    n = len(summaries.ss)
    if n == 0:
        return BootstrapInterval(None, level, n_resamples, 0, seed)
    rng = np.random.default_rng(seed)
    rates = []
    for start in range(0, n_resamples, _BATCH):
        batch = min(_BATCH, n_resamples - start)
        draws = rng.integers(0, n, size=(batch, n))
        # Count each row's draws in one bincount by giving every row its own block of n bins.
        offsets = draws + n * np.arange(batch)[:, None]
        counts = np.bincount(offsets.ravel(), minlength=batch * n).reshape(batch, n)
        rates.append(resampled_rates(summaries, counts.astype(np.float64), verdict))

    all_rates = np.concatenate(rates)
    valid = all_rates[~np.isnan(all_rates)]
    ci = percentile_interval(valid, level) if valid.size else None
    return BootstrapInterval(ci, level, n_resamples, int(valid.size), seed)
