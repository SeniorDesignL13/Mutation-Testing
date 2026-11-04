"""Dose-response curves: how a judge's response to an operator grows with the edit's intensity.

A parameterized operator (padding at 50, 100, 200 and 400 words, say) is scored at several
intensities, and each gets its own rate: detection rate for a degrading operator, spurious
sensitivity for a preserving one. Both count a mutant as a hit when its score moves by more than
noise explains, and a judge that ignores the edit is hit on about alpha of mutants. This module
fits one curve through those rates and estimates the onset: the intensity at which the judge's
hit rate first rises a set amount above chance. For a preserving operator that's where the judge
starts reacting to an edit it should ignore. For a degrading one it's the smallest edit the
judge starts to catch, and it misses edits smaller than that.

The curve is the log-logistic model used for benchmark doses in toxicology, with its floor
fixed at alpha (what a judge that ignores the edit scores):

    P(d)  = alpha + (1 - alpha) * ER(d)
    ER(d) = 1 / (1 + exp(-(logit(r) + b * ln(d / D))))

ER is the extra hit rate over chance, scaled to [0, 1]. D, the onset, is the intensity at which
ER reaches the benchmark r (10% by default), and b is the slope on log intensity. Both are
fitted by maximum likelihood to the hit counts at each intensity, and the onset's interval comes
from the profile likelihood. docs/metrics.md ("Dose-response curve") explains the choices.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import brentq, minimize_scalar
from scipy.special import expit, log_expit, logit
from scipy.stats import chi2

from mtlj.stats.detection import CI_LEVEL, DEFAULT_ALPHA, DetectionRate
from mtlj.stats.spurious import SpuriousSensitivity

# Extra hit rate over chance that marks the onset.
DEFAULT_BENCHMARK = 0.10
# Slope b on log intensity. At least 1: at small intensities the extra hit rate grows no faster
# than the intensity itself, the usual restriction for this model in benchmark-dose software.
# Without it the onset can run off towards 0 whenever the data can't place it. The upper bound
# only keeps a step finite: at b = 30, ER goes from 10% to 90% within a 16% change in intensity.
MIN_SLOPE = 1.0
MAX_SLOPE = 30.0
# How far beyond the tested intensities the onset is searched for, in units of ln(intensity):
# a factor of e^15, about 3 million, either way.
_REACH = 15.0
_GRID_STEP = 0.05
_SLOPES = np.geomspace(MIN_SLOPE, MAX_SLOPE, 61)

Floats = NDArray[np.float64]
Rate = DetectionRate | SpuriousSensitivity


@dataclass(frozen=True, slots=True)
class DosePoint:
    """The hits and testable mutants at one intensity."""

    intensity: float
    hits: int
    testable: int


@dataclass(frozen=True, slots=True)
class DoseResponse:
    """A fitted dose-response curve for one operator, judge and judge config.

    ``points`` are the intensities the curve was fitted to: those with a testable mutant.

    ``onset`` is the intensity at which the hit rate is ``benchmark`` of the way from chance
    (``alpha``) to 1. It's None when the best fit is no response at any intensity, and 0 when
    the judge hits every mutant at every tested intensity, so the onset lies below all of them.
    ``slope`` is None in both cases.

    ``onset_ci`` is the onset's profile-likelihood interval at ``level``. Its upper end is
    ``math.inf`` when the data can't rule out a judge that never responds, and its lower end is
    0 when they can't rule out an onset below every tested intensity. ``responds`` is True when
    the upper end is finite: the hit rate rises above chance somewhere. ``in_range`` is True
    when the onset lies between the lowest and highest tested intensity.

    ``fit_p_value`` tests how well the curve fits the observed rates (Pearson chi-square with
    intensities - 2 degrees of freedom). A small value says the rates don't follow this shape.
    None with two intensities, where there's nothing left to test.
    """

    points: tuple[DosePoint, ...]
    onset: float | None
    onset_ci: tuple[float, float]
    slope: float | None
    responds: bool
    in_range: bool
    fit_p_value: float | None
    alpha: float
    benchmark: float
    level: float

    def rate(self, intensity: float) -> float:
        """The fitted hit rate at ``intensity``."""
        if not intensity >= 0:
            raise ValueError(f"intensity must be >= 0, got {intensity}")
        if intensity == 0 or self.onset is None:
            return self.alpha
        if self.onset == 0 or self.slope is None:
            return 1.0
        x = logit(self.benchmark) + self.slope * math.log(intensity / self.onset)
        return self.alpha + (1 - self.alpha) * float(expit(x))


class _Likelihood:
    """Log-likelihood of the curve on a set of points, as a function of ln(onset) and slope."""

    def __init__(self, points: tuple[DosePoint, ...], alpha: float, benchmark: float) -> None:
        self.alpha = alpha
        self.base = float(logit(benchmark))
        self.log_d = np.array([math.log(p.intensity) for p in points])
        self.hits = np.array([p.hits for p in points], dtype=np.float64)
        self.misses = np.array([p.testable - p.hits for p in points], dtype=np.float64)
        # The two limits of the onset. At +inf the judge is at chance everywhere. At 0 it hits
        # every mutant, which is only possible (likelihood above 0) if every mutant was a hit.
        self.at_none = float(np.sum(self.hits * math.log(alpha) + self.misses * math.log1p(-alpha)))
        self.at_zero = -math.inf if self.misses.any() else 0.0

    def __call__(self, log_onset: Floats | float, slope: Floats | float) -> Floats:
        """Log-likelihood, broadcast over ``log_onset`` and ``slope``."""
        m = np.asarray(log_onset, dtype=np.float64)[..., None]
        b = np.asarray(slope, dtype=np.float64)[..., None]
        x = self.base + b * (self.log_d - m)
        log_hit = np.log(self.alpha + (1 - self.alpha) * expit(x))
        log_miss = math.log1p(-self.alpha) + log_expit(-x)
        return np.sum(self.hits * log_hit + self.misses * log_miss, axis=-1)

    def profile(self, log_onset: float) -> tuple[float, float]:
        """The best log-likelihood at this onset over the slope, and the slope that gives it."""
        values = self(log_onset, _SLOPES)
        i = int(np.argmax(values))
        best, slope = float(values[i]), float(_SLOPES[i])
        low, high = _SLOPES[max(i - 1, 0)], _SLOPES[min(i + 1, len(_SLOPES) - 1)]
        refined = minimize_scalar(
            lambda b: -float(self(log_onset, b)),
            bounds=(low, high),
            method="bounded",
            options={"xatol": 1e-6},
        )
        if -refined.fun > best:
            best, slope = float(-refined.fun), float(refined.x)
        return best, slope


def fit_dose_response(
    points: Iterable[DosePoint],
    alpha: float = DEFAULT_ALPHA,
    benchmark: float = DEFAULT_BENCHMARK,
    level: float = CI_LEVEL,
) -> DoseResponse:
    """Fit the dose-response curve to one operator's hit counts across intensities.

    Args:
        points: hits and testable mutants at each intensity. Intensities with no testable mutant
            are left out.
        alpha: the chance hit rate, the curve's floor: the alpha the rates were computed with.
        benchmark: the extra hit rate over chance, as a share of the way from alpha to 1, that
            marks the onset.
        level: confidence level of the onset's interval.

    Raises:
        ValueError: for an argument out of range, a repeated intensity, hits outside
            [0, testable], or fewer than two intensities with a testable mutant.
    """
    for name, value in (("alpha", alpha), ("benchmark", benchmark), ("level", level)):
        if not 0 < value < 1:
            raise ValueError(f"{name} must be in (0, 1), got {value}")
    points = tuple(sorted(_checked(points), key=lambda p: p.intensity))
    fitted = tuple(p for p in points if p.testable > 0)
    if len(fitted) < 2:
        raise ValueError(
            f"a curve needs two or more intensities with a testable mutant, got {len(fitted)}"
        )

    lik = _Likelihood(fitted, alpha, benchmark)
    grid = np.arange(lik.log_d[0] - _REACH, lik.log_d[-1] + _REACH + _GRID_STEP / 2, _GRID_STEP)
    # Best log-likelihood at each grid onset, over the slope grid. A lower bound on the profile,
    # good enough to find where it peaks and where it crosses the interval's cutoff.
    coarse = lik(grid[:, None], _SLOPES[None, :]).max(axis=1)

    i = int(np.argmax(coarse))
    log_onset, best, slope = _refine_peak(lik, grid, i)
    if lik.at_none >= best:
        log_onset, best, slope, i = math.inf, lik.at_none, None, len(grid) - 1
    elif lik.at_zero >= best:
        log_onset, best, slope, i = -math.inf, lik.at_zero, None, 0

    cutoff = best - float(chi2.ppf(level, 1)) / 2
    high = (
        math.inf
        if lik.at_none >= cutoff
        else _crossing(lik, grid, coarse, cutoff, i, log_onset, step=1)
    )
    low = 0.0 if lik.at_zero >= cutoff else _crossing(lik, grid, coarse, cutoff, i, log_onset, -1)
    onset = None if log_onset == math.inf else math.exp(log_onset)

    result = DoseResponse(
        points=fitted,
        onset=onset,
        onset_ci=(low, high),
        slope=slope,
        responds=high < math.inf,
        in_range=onset is not None and fitted[0].intensity <= onset <= fitted[-1].intensity,
        fit_p_value=None,
        alpha=alpha,
        benchmark=benchmark,
        level=level,
    )
    return _with_fit_test(result)


def dose_points(results: Mapping[float, Rate]) -> tuple[DosePoint, ...]:
    """One operator's hit counts at each intensity, from its rates there.

    A hit is a detected mutant for a detection rate and a flagged one for spurious sensitivity.
    """
    return tuple(
        DosePoint(
            intensity,
            r.detected if isinstance(r, DetectionRate) else r.flagged,
            r.testable,
        )
        for intensity, r in sorted(results.items())
    )


def fit_operator(
    results: Mapping[float, Rate],
    benchmark: float = DEFAULT_BENCHMARK,
    level: float = CI_LEVEL,
) -> DoseResponse:
    """Fit the dose-response curve to one operator's rates, keyed by intensity.

    The rates must all be of one kind and one judge and judge config; alpha is theirs.

    Raises:
        ValueError: if the rates use different alphas, or as ``fit_dose_response``.
    """
    alphas = {r.alpha for r in results.values()}
    if len(alphas) > 1:
        raise ValueError(f"rates use different alphas: {sorted(alphas)}")
    alpha = alphas.pop() if alphas else DEFAULT_ALPHA
    return fit_dose_response(dose_points(results), alpha, benchmark, level)


def fit_operators(
    results: Mapping[str, Mapping[float, Rate]],
    benchmark: float = DEFAULT_BENCHMARK,
    level: float = CI_LEVEL,
) -> dict[str, DoseResponse]:
    """Fit a curve for every operator in a run that was scored at two or more intensities.

    ``results`` holds each operator's rates by intensity, for one judge and judge config. An
    operator with fewer than two intensities that have a testable mutant has no curve and is
    left out.
    """
    return {
        operator: fit_operator(by_intensity, benchmark, level)
        for operator, by_intensity in results.items()
        if sum(r.testable > 0 for r in by_intensity.values()) >= 2
    }


def _checked(points: Iterable[DosePoint]) -> list[DosePoint]:
    points = list(points)
    for p in points:
        if not (math.isfinite(p.intensity) and p.intensity > 0):
            raise ValueError(f"intensity must be finite and > 0, got {p.intensity}")
        if not 0 <= p.hits <= p.testable:
            raise ValueError(f"need 0 <= hits <= testable, got {p.hits} of {p.testable}")
    intensities = [p.intensity for p in points]
    if len(set(intensities)) < len(intensities):
        raise ValueError(f"intensities must be distinct, got {sorted(intensities)}")
    return points


def _refine_peak(lik: _Likelihood, grid: Floats, i: int) -> tuple[float, float, float]:
    """The profile's peak near grid point i: ln(onset), log-likelihood and slope."""
    best, slope = lik.profile(float(grid[i]))
    log_onset = float(grid[i])
    if 0 < i < len(grid) - 1:
        refined = minimize_scalar(
            lambda m: -lik.profile(m)[0],
            bounds=(float(grid[i - 1]), float(grid[i + 1])),
            method="bounded",
            options={"xatol": 1e-6},
        )
        value, at_slope = lik.profile(float(refined.x))
        if value > best:
            log_onset, best, slope = float(refined.x), value, at_slope
    return log_onset, best, slope


def _crossing(
    lik: _Likelihood,
    grid: Floats,
    coarse: Floats,
    cutoff: float,
    start: int,
    peak: float,
    step: int,
) -> float:
    """Where the profile falls below ``cutoff``, walking the grid from ``start`` by ``step``.

    Returns the onset there, or the limit of the search (0 or inf) if it never falls below.
    """

    def above(m: float) -> float:
        return lik.profile(m)[0] - cutoff

    j = start + step
    while 0 <= j < len(grid):
        # coarse is at most the profile, so only a refined value below the cutoff counts.
        if coarse[j] < cutoff and above(float(grid[j])) < 0:
            # Bracket the crossing from the last grid point above the cutoff, or from the peak
            # when the profile is narrower than the grid (with many mutants per intensity).
            inner = float(grid[j - step])
            if above(inner) < 0:
                inner = peak
            return math.exp(brentq(above, *sorted((inner, float(grid[j]))), xtol=1e-9))
        j += step
    return math.inf if step > 0 else 0.0


def _with_fit_test(result: DoseResponse) -> DoseResponse:
    """``result`` with its Pearson chi-square goodness-of-fit p-value filled in."""
    df = len(result.points) - 2
    if df < 1:
        return result
    statistic = 0.0
    for p in result.points:
        rate = result.rate(p.intensity)
        variance = p.testable * rate * (1 - rate)
        if variance > 0:
            statistic += (p.hits - p.testable * rate) ** 2 / variance
    return replace(result, fit_p_value=float(chi2.sf(statistic, df)))
