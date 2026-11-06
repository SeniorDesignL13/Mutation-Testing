"""Multiple-comparison correction: Benjamini-Hochberg across the tests in one run.

An audit runs one test per operator and intensity, each at alpha = 0.05. With m tests, a judge
that ignores every change still comes out "significant" on about alpha * m of them. Benjamini-
Hochberg adjusts each p-value for the size of the family, so that among the results called
significant the expected share of false ones (the false discovery rate) is at most q.

The tests are the exact binomial tests against alpha: detection rate's for each degrading
operator and intensity (``mtlj.stats.detection``) and spurious sensitivity's for each preserving
one (``mtlj.stats.spurious``). The family is all of them for one judge and judge config in a
run. ``benjamini_hochberg`` corrects any family of p-values, so a test added later joins the
same family. docs/metrics.md ("Multiple-comparison correction") explains the choices.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass

from scipy.stats import false_discovery_control

from mtlj.stats.detection import DEFAULT_ALPHA, DetectionRate
from mtlj.stats.spurious import SpuriousSensitivity

METHOD = "benjamini-hochberg"


@dataclass(frozen=True, slots=True)
class CorrectedTest:
    """One test's p-value before and after correction, and its verdict each way.

    ``significant`` is the uncorrected verdict, ``p_value < q``. ``significant_corrected`` is
    ``adjusted < q``. A test with no p-value (no testable mutant) has None for both values, is
    significant neither way and isn't counted in the family.
    """

    p_value: float | None
    adjusted: float | None
    significant: bool
    significant_corrected: bool


@dataclass(frozen=True, slots=True)
class Correction[K: Hashable]:
    """A family of tests corrected together. ``n_tests`` counts those with a p-value."""

    tests: Mapping[K, CorrectedTest]
    q: float
    n_tests: int
    method: str = METHOD


def benjamini_hochberg[K: Hashable](
    p_values: Mapping[K, float | None], q: float = DEFAULT_ALPHA
) -> Correction[K]:
    """Benjamini-Hochberg adjusted p-values for one family of tests.

    The adjusted p-value of the i-th smallest of m p-values is the smallest m * p_(j) / j over
    j >= i, capped at 1. A test is significant after correction when its adjusted p-value is
    below ``q``, which is the step-up procedure of Benjamini and Hochberg (1995).

    Args:
        p_values: each test's p-value, by any key (an operator and intensity, say). None marks
            a test that couldn't be run; it's left out of the family.
        q: the false discovery rate to control, and the cutoff for the uncorrected verdict.

    Raises:
        ValueError: if ``q`` isn't in (0, 1) or a p-value isn't in [0, 1].
    """
    if not 0 < q < 1:
        raise ValueError(f"q must be in (0, 1), got {q}")
    for key, p in p_values.items():
        if p is not None and not 0 <= p <= 1:
            raise ValueError(f"p-value for {key!r} must be in [0, 1], got {p}")

    keys = [key for key, p in p_values.items() if p is not None]
    adjusted = (
        dict(zip(keys, false_discovery_control([p_values[k] for k in keys]), strict=True))
        if keys
        else {}
    )
    tests: dict[K, CorrectedTest] = {}
    for key, p in p_values.items():
        a = None if p is None else float(adjusted[key])
        tests[key] = CorrectedTest(
            p_value=p,
            adjusted=a,
            significant=p is not None and p < q,
            significant_corrected=a is not None and a < q,
        )
    return Correction(tests, q, len(keys))


def correct_above_chance[K: Hashable](
    results: Mapping[K, DetectionRate | SpuriousSensitivity],
) -> Correction[K]:
    """Correct the above-chance tests of one family of detection and spurious results.

    Pass every operator and intensity of one judge and judge config in a run, degrading and
    preserving alike, keyed however the report needs (``variance.Condition`` fits). ``q`` is the
    results' alpha, so the uncorrected verdict is each result's ``above_chance``.

    Raises:
        ValueError: if the results don't all use the same alpha.
    """
    alphas = {r.alpha for r in results.values()}
    if len(alphas) > 1:
        raise ValueError(f"results use different alphas: {sorted(alphas)}")
    q = alphas.pop() if alphas else DEFAULT_ALPHA
    return benjamini_hochberg({key: r.p_value for key, r in results.items()}, q)
