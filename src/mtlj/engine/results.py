"""The audit result artifact: one structured file every consumer of a run reads.

``build_results`` turns a run's mutants and evaluations into ``AuditResults``, and
``write_results`` stores them as ``results.json`` in the run's directory, next to the raw
``evaluations.jsonl``. docs/results.md documents the format. It holds no timestamps, so the
same run always gives a byte-identical file.

Each operator gets the headline metric of its family (docs/metrics.md): detection rate for
degrading operators, spurious sensitivity for preserving ones. Each is reported pooled across
the operator's intensities and at each intensity, with a Wilson and a bootstrap interval and
which of the two is the headline. A record appears once per intensity in the pooled figure, and
those mutants share one original, so one unlucky draw can move them together. The pooled Wilson
interval treats those mutants as independent, so it's too narrow, and the binomial test, which
assumes independence too, is left out of it. The bootstrap resamples whole records, so its pooled
interval holds.

The binomial tests at each intensity, of every operator in the run, are one family for the
Benjamini-Hochberg correction (``mtlj.stats.correction``). An operator scored at two or more
intensities also gets its dose-response curve (``mtlj.stats.dose_response``).

A file is only ever written with metrics for a finished run. An aborted run's rates would mix
scored mutants with ones that were never reached, so ``operators`` stays empty and the rows
that were scored stay in ``evaluations.jsonl``.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from mtlj.engine.generator import NO_CHANGE, OPERATOR_ERROR, GenerationResult
from mtlj.engine.runner import OK, ORIGINAL, Evaluation
from mtlj.judges.base import Judge
from mtlj.operators.base import OperatorFamily
from mtlj.stats.bootstrap import BootstrapInterval
from mtlj.stats.correction import CorrectedTest, correct_above_chance
from mtlj.stats.detection import (
    CI_LEVEL,
    DEFAULT_ALPHA,
    DetectionRate,
    MutantScores,
    detection_rate,
    noise_floor,
)
from mtlj.stats.dose_response import DoseResponse, fit_operators
from mtlj.stats.paired import effect_size, paired_delta
from mtlj.stats.spurious import SpuriousSensitivity, spurious_sensitivity
from mtlj.stats.variance import Condition, condition_variances

# The format's version. read_results rejects every other one. Bump it only for a breaking change:
# a field removed, renamed or given a new meaning. A new field doesn't bump it; add it to the
# models and to docs/results.md.
SCHEMA_VERSION = 1
# The file a run's results are saved as, in the run's directory.
RESULTS_FILE = "results.json"

COMPLETE = "complete"
ABORTED = "aborted"  # The run stopped early.
INSUFFICIENT_DATA = "insufficient_data"  # No original has the 2+ scores that measure noise.

# From this many testable mutants, the bootstrap interval is the headline one; below it, Wilson,
# where the bootstrap under-covers (docs/metrics.md, "Bootstrap intervals").
BOOTSTRAP_HEADLINE_MIN = 100
BOOTSTRAP = "bootstrap"
WILSON = "wilson"

Status = Literal["complete", "aborted", "insufficient_data"]
Interval = tuple[float, float]
Rate = DetectionRate | SpuriousSensitivity


class _Model(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}


class JudgeInfo(_Model):
    """Which judge produced a run."""

    name: str
    model: str
    version: str


class Scorings(_Model):
    """How many judge calls a run stored, and how they ended."""

    total: int
    ok: int
    errors: int
    errors_by_type: dict[str, int]


class RunInfo(_Model):
    """How a run was made and how it ended. ``reason`` says why when it isn't complete."""

    judge: JudgeInfo
    dataset: str
    n_records: int
    samples: int
    alpha: float
    ci_level: float
    base_seed: int
    battery: dict[str, list[float]]
    drop_repeated_doses: bool
    status: Status
    reason: str | None
    scorings: Scorings


class Noise(_Model):
    """The judge's run-to-run noise, measured on the originals."""

    sigma: float
    n_originals: int


class PairedDeltaResult(_Model):
    """Mean drop in score across mutants, each compared with its own original."""

    mean: float | None
    ci: Interval | None
    sd: float | None
    n: int


class BootstrapResult(_Model):
    """A rate's bootstrap interval, with what's needed to reproduce it (``mtlj.stats.bootstrap``).

    ``ci`` is None when no resample had a rate.
    """

    ci: Interval | None
    level: float
    n_resamples: int
    n_valid: int
    seed: int


class _Rate(_Model):
    """What every rate reports.

    ``rate``, ``wilson_ci``, ``bootstrap_ci`` and ``headline_interval`` are None when no mutant
    is testable. ``headline_interval`` names the interval to show first: ``bootstrap`` from
    ``BOOTSTRAP_HEADLINE_MIN`` testable mutants, ``wilson`` below that.

    ``p_value`` and ``above_chance`` are the one-sided exact binomial test of the hits against
    alpha. ``p_adjusted`` and ``above_chance_corrected`` are the same test after the
    Benjamini-Hochberg correction across the run's tests (``AuditResults.correction``). All four
    are None when the test wasn't run: when no mutant is testable, and when pooled across
    intensities, since the test assumes independent mutants.
    """

    rate: float | None
    wilson_ci: Interval | None
    bootstrap_ci: BootstrapResult | None
    headline_interval: Literal["bootstrap", "wilson"] | None
    testable: int
    excluded: dict[str, int]
    tau: float
    n_samples: int
    paired_delta: PairedDeltaResult
    effect_size: float | None
    p_value: float | None
    above_chance: bool | None
    p_adjusted: float | None
    above_chance_corrected: bool | None

    @property
    def headline_ci(self) -> Interval | None:
        """The interval ``headline_interval`` names."""
        if self.headline_interval == BOOTSTRAP and self.bootstrap_ci is not None:
            return self.bootstrap_ci.ci
        return self.wilson_ci


class DetectionResult(_Rate):
    """Detection rate of a degrading operator's mutants."""

    metric: Literal["detection_rate"] = "detection_rate"
    detected: int


class SpuriousResult(_Rate):
    """Spurious sensitivity of a preserving operator's mutants, read against alpha."""

    metric: Literal["spurious_sensitivity"] = "spurious_sensitivity"
    flagged: int
    penalized: int
    rewarded: int


class IntensityResult(_Model):
    """One operator's result at one intensity."""

    intensity: float
    result: DetectionResult | SpuriousResult


class DosePointResult(_Model):
    """One intensity the curve was fitted to, with the curve's rate there."""

    intensity: float
    hits: int
    testable: int
    fitted_rate: float


class DoseResponseResult(_Model):
    """An operator's dose-response curve and onset (``mtlj.stats.dose_response``).

    ``onset`` is None when the best fit is no response at any tested intensity. ``onset_ci``'s
    upper end is None when the data can't rule out a judge that never responds: it's infinite in
    ``DoseResponse``, and JSON can't hold that.
    """

    onset: float | None
    onset_ci: tuple[float, float | None]
    slope: float | None
    responds: bool
    in_range: bool
    fit_p_value: float | None
    alpha: float
    benchmark: float
    level: float
    points: list[DosePointResult]


class OperatorResult(_Model):
    """One operator's result, pooled across its intensities and at each one.

    ``operator_errors`` counts mutants the operator crashed on. They're in no rate's denominator.
    ``dose_response`` is None unless two or more intensities above 0 have a testable mutant.
    """

    operator: str
    family: OperatorFamily
    operator_errors: int
    overall: DetectionResult | SpuriousResult
    by_intensity: list[IntensityResult]
    dose_response: DoseResponseResult | None


class CorrectionInfo(_Model):
    """The family of tests corrected together: every operator's test at every intensity.

    ``n_tests`` counts the tests that were run (those with a testable mutant). ``q`` is alpha.
    """

    method: str
    q: float
    n_tests: int


class AuditResults(_Model):
    """A run's results. ``noise``, ``correction`` and ``variance`` are None, and ``operators``
    empty, unless the run's status is ``complete``.

    ``variance`` is ``mtlj.stats.variance``'s report as it stands, and that module owns its shape.
    """

    schema_version: int = SCHEMA_VERSION
    run: RunInfo
    noise: Noise | None
    operators: list[OperatorResult]
    correction: CorrectionInfo | None
    variance: dict[str, Any] | None


def _bootstrap(interval: BootstrapInterval | None) -> BootstrapResult | None:
    if interval is None:
        return None
    return BootstrapResult(
        ci=interval.ci,
        level=interval.level,
        n_resamples=interval.n_resamples,
        n_valid=interval.n_valid,
        seed=interval.seed,
    )


def headline_interval(testable: int) -> Literal["bootstrap", "wilson"] | None:
    """Which interval to show first for a rate over ``testable`` mutants."""
    if not testable:
        return None
    return BOOTSTRAP if testable >= BOOTSTRAP_HEADLINE_MIN else WILSON


def _result(rate: Rate, test: CorrectedTest | None) -> DetectionResult | SpuriousResult:
    """``rate`` as stored in the file. ``test`` is None when the binomial test is left out."""
    delta = paired_delta(rate.outcomes)
    tested = test is not None and test.p_value is not None
    fields: dict[str, Any] = {
        "rate": rate.rate,
        "wilson_ci": rate.ci,
        "bootstrap_ci": _bootstrap(rate.bootstrap),
        "headline_interval": headline_interval(rate.testable),
        "testable": rate.testable,
        "excluded": {reason.value: count for reason, count in rate.excluded.items()},
        "tau": rate.tau,
        "n_samples": rate.n_samples,
        "paired_delta": PairedDeltaResult(mean=delta.mean, ci=delta.ci, sd=delta.sd, n=delta.n),
        "effect_size": effect_size(delta, rate.sigma),
        "p_value": test.p_value if tested else None,
        "above_chance": test.significant if tested else None,
        "p_adjusted": test.adjusted if tested else None,
        "above_chance_corrected": test.significant_corrected if tested else None,
    }
    if isinstance(rate, DetectionRate):
        return DetectionResult(**fields, detected=rate.detected)
    return SpuriousResult(
        **fields, flagged=rate.flagged, penalized=rate.penalized, rewarded=rate.rewarded
    )


def _rate(
    family: OperatorFamily,
    originals: dict[str, list[float]],
    mutants: Sequence[MutantScores],
    alpha: float,
) -> Rate:
    """Score one group of mutants with its family's headline metric."""
    if family is OperatorFamily.DEGRADING:
        return detection_rate(originals, mutants, alpha)
    return spurious_sensitivity(originals, mutants, alpha)


def _dose_response(curve: DoseResponse | None) -> DoseResponseResult | None:
    if curve is None:
        return None
    low, high = curve.onset_ci
    return DoseResponseResult(
        onset=curve.onset,
        onset_ci=(low, None if math.isinf(high) else high),
        slope=curve.slope,
        responds=curve.responds,
        in_range=curve.in_range,
        fit_p_value=curve.fit_p_value,
        alpha=curve.alpha,
        benchmark=curve.benchmark,
        level=curve.level,
        points=[
            DosePointResult(
                intensity=p.intensity,
                hits=p.hits,
                testable=p.testable,
                fitted_rate=curve.rate(p.intensity),
            )
            for p in curve.points
        ],
    )


def operator_families(generation: GenerationResult) -> dict[str, OperatorFamily]:
    """Every operator's family, from the generation or from its mutants.

    Raises:
        ValueError: if an operator in the battery has neither. run_audit checks this before
            any scoring, so a run can't be paid for and then fail here.
    """
    families = {m.operator_name: m.operator_family for m in generation.mutants}
    families.update(generation.families)
    missing = sorted(set(generation.battery) - families.keys())
    if missing:
        raise ValueError(f"no family recorded for operators {missing}")
    return families


def _group_mutants(
    generation: GenerationResult, scores: dict[str, list[float]]
) -> dict[tuple[str, float], list[MutantScores]]:
    """Every mutant by (operator, intensity), with what ``skipped`` holds of them.

    Unchanged mutants are skipped before scoring, so without them here the no-change count
    the metrics report would always be 0.
    """
    groups: dict[tuple[str, float], list[MutantScores]] = defaultdict(list)
    for operator, intensities in generation.battery.items():
        for intensity in intensities:
            groups[(operator, intensity)] = []
    for mutant in generation.mutants:
        key = (mutant.operator_name, mutant.intensity)
        groups[key].append(MutantScores(mutant.record_id, scores.get(mutant.mutant_id, [])))
    for skipped in generation.skipped:
        if skipped.reason == NO_CHANGE:
            key = (skipped.operator_name, skipped.intensity)
            groups[key].append(MutantScores(skipped.record_id, [], changed=False))
    return groups


def _operator_results(
    generation: GenerationResult,
    originals: dict[str, list[float]],
    scores: dict[str, list[float]],
    alpha: float,
) -> tuple[list[OperatorResult], CorrectionInfo]:
    groups = _group_mutants(generation, scores)
    families = operator_families(generation)
    crashes = Counter(s.operator_name for s in generation.skipped if s.reason == OPERATOR_ERROR)
    rates = {key: _rate(families[key[0]], originals, group, alpha) for key, group in groups.items()}
    # One family: every operator's test at every intensity, degrading and preserving alike
    # (docs/metrics.md, "Multiple-comparison correction"). Pooled figures aren't tested.
    correction = correct_above_chance(rates)
    operators = sorted({name for name, _ in groups})
    # Fitted on intensities above 0. At 0 nothing changes, so the curve is at alpha there.
    curves = fit_operators(
        {
            operator: {i: rate for (name, i), rate in rates.items() if name == operator and i > 0}
            for operator in operators
        }
    )

    results = []
    for operator in operators:
        family = families[operator]
        intensities = sorted(i for name, i in groups if name == operator)
        if len(intensities) == 1:
            # Nothing to pool: the overall figure is the one intensity's, test included.
            key = (operator, intensities[0])
            overall = _result(rates[key], correction.tests[key])
        else:
            pooled = [m for intensity in intensities for m in groups[(operator, intensity)]]
            overall = _result(_rate(family, originals, pooled, alpha), None)
        results.append(
            OperatorResult(
                operator=operator,
                family=family,
                operator_errors=crashes[operator],
                overall=overall,
                by_intensity=[
                    IntensityResult(
                        intensity=intensity,
                        result=_result(
                            rates[(operator, intensity)], correction.tests[(operator, intensity)]
                        ),
                    )
                    for intensity in intensities
                ],
                dose_response=_dose_response(curves.get(operator)),
            )
        )
    info = CorrectionInfo(method=correction.method, q=correction.q, n_tests=correction.n_tests)
    return results, info


def _variance(
    generation: GenerationResult,
    originals: dict[str, list[float]],
    scores: dict[str, list[float]],
) -> dict[str, Any]:
    conditions: dict[Condition, dict[str, list[float]]] = defaultdict(dict)
    for operator, intensities in generation.battery.items():
        for intensity in intensities:
            conditions[Condition(operator, intensity)] = {}
    for mutant in generation.mutants:
        condition = Condition(mutant.operator_name, mutant.intensity)
        conditions[condition][mutant.mutant_id] = scores.get(mutant.mutant_id, [])
    report = condition_variances(originals, conditions).to_dict()
    # Through JSON, so these equal what read_results gets back (its tuples become lists).
    return json.loads(json.dumps(report))


def build_results(
    generation: GenerationResult,
    evaluations: Iterable[Evaluation],
    *,
    judge: Judge,
    dataset: str,
    samples: int,
    alpha: float = DEFAULT_ALPHA,
    aborted: str | None = None,
) -> AuditResults:
    """The results of one judge's run.

    Args:
        generation: the run's originals, mutants and skipped mutants.
        evaluations: every evaluation the run stored, failed ones included.
        judge: the judge that made them.
        dataset: which dataset the records came from, such as ``gold``.
        samples: how many times each text was meant to be scored.
        alpha: the false-detection rate. docs/metrics.md fixes it at 0.05.
        aborted: why the run stopped early, if it did.

    Raises:
        ValueError: if an evaluation came from a different judge. Each judge config's run has
            its own results, since a rate belongs to a judge and its config.
    """
    evaluations = list(evaluations)
    info = JudgeInfo(name=judge.name, model=judge.model, version=judge.version)
    for evaluation in evaluations:
        found = (evaluation.judge_name, evaluation.model, evaluation.version)
        if found != (info.name, info.model, info.version):
            raise ValueError(
                f"evaluation {evaluation.target_id!r} came from another judge: {found}"
            )

    originals: dict[str, list[float]] = {r.record_id: [] for r in generation.originals}
    scores: dict[str, list[float]] = defaultdict(list)
    for evaluation in evaluations:
        if evaluation.status == OK:
            if evaluation.kind == ORIGINAL:
                originals[evaluation.record_id].append(evaluation.score)
            else:
                scores[evaluation.target_id].append(evaluation.score)

    measurable = sum(len(s) >= 2 for s in originals.values())
    status: Status = COMPLETE
    reason = None
    if aborted is not None:
        status, reason = ABORTED, aborted
    elif not measurable:
        status = INSUFFICIENT_DATA
        reason = "no original has the two or more scores needed to measure noise"

    errors = Counter(e.error_type for e in evaluations if e.status != OK)
    run = RunInfo(
        judge=info,
        dataset=dataset,
        n_records=len(generation.originals),
        samples=samples,
        alpha=alpha,
        ci_level=CI_LEVEL,
        base_seed=generation.base_seed,
        battery=generation.battery,
        drop_repeated_doses=generation.drop_repeated_doses,
        status=status,
        reason=reason,
        scorings=Scorings(
            total=len(evaluations),
            ok=len(evaluations) - errors.total(),
            errors=errors.total(),
            errors_by_type={str(kind): count for kind, count in sorted(errors.items())},
        ),
    )
    if status != COMPLETE:
        return AuditResults(run=run, noise=None, operators=[], correction=None, variance=None)
    operators, correction = _operator_results(generation, originals, scores, alpha)
    return AuditResults(
        run=run,
        noise=Noise(sigma=noise_floor(originals.values()), n_originals=measurable),
        operators=operators,
        correction=correction,
        variance=_variance(generation, originals, scores),
    )


def write_results(results: AuditResults, run_dir: Path) -> Path:
    """Store ``results`` as ``results.json`` in the run's directory.

    It's written beside the final name and then moved into place, so a process killed part way
    can't leave a half-written file for the dashboard or CI to read.
    """
    path = run_dir / RESULTS_FILE
    partial = path.with_suffix(".json.partial")
    text = json.dumps(results.model_dump(mode="json"), indent=2, sort_keys=True, allow_nan=False)
    partial.write_text(text + "\n", encoding="utf-8", newline="\n")
    partial.replace(path)
    return path


def read_results(path: Path) -> AuditResults:
    """Read a ``results.json`` file, or the one in a run's directory.

    Raises:
        ValueError: if it was written in a format this version doesn't read.
    """
    if path.is_dir():
        path = path / RESULTS_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    found = data.get("schema_version") if isinstance(data, dict) else None
    if found != SCHEMA_VERSION:
        raise ValueError(f"{path} has schema version {found!r}, expected {SCHEMA_VERSION}")
    return AuditResults.model_validate(data)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _interval(result: DetectionResult | SpuriousResult) -> str:
    """The headline interval, saying which one it is."""
    ci = result.headline_ci
    if ci is None:
        return ""
    name = "bootstrap" if result.headline_interval == BOOTSTRAP else "Wilson"
    return f" [{ci[0]:.1%}, {ci[1]:.1%}] ({name})"


def _verdict(result: DetectionResult | SpuriousResult) -> str:
    if result.above_chance_corrected:
        return ", above chance"
    if result.above_chance:
        return ", above chance before correction only"
    return ""


def _rate_line(label: str, result: DetectionResult | SpuriousResult) -> str:
    rate = f"{_percent(result.rate)}{_interval(result)}"
    mean = result.paired_delta.mean
    delta = "" if mean is None else f", mean drop {mean:+.3f}"
    if isinstance(result, DetectionResult):
        counts = f"{result.detected}/{result.testable} detected"
    else:
        counts = (
            f"{result.flagged}/{result.testable} flagged "
            f"({result.penalized} penalized, {result.rewarded} rewarded)"
        )
    excluded = ", ".join(f"{reason} {n}" for reason, n in result.excluded.items() if n)
    skipped = f", excluded {excluded}" if excluded else ""
    return f"{label}: {rate}, {counts}{_verdict(result)}{delta}{skipped}"


def _dose_line(curve: DoseResponseResult) -> str:
    if curve.onset is None:
        return "dose-response: no response at the tested intensities"
    low, high = curve.onset_ci
    upper = "inf)" if high is None else f"{high:.3g}]"
    line = f"dose-response: onset {curve.onset:.3g} [{low:.3g}, {upper}"
    if not curve.responds:
        line += ", response not established"
    elif not curve.in_range:
        line += ", outside the tested intensities"
    return line


def format_summary(results: AuditResults) -> str:
    """A run's results as text for the terminal."""
    run = results.run
    judge = run.judge
    scorings = run.scorings
    lines = [
        f"Judge: {judge.name} ({judge.model}, version {judge.version})",
        f"Dataset: {run.dataset} ({run.n_records} records)",
        f"Status: {run.status}" + (f" ({run.reason})" if run.reason else ""),
        f"Samples: {run.samples}, alpha: {run.alpha}",
        f"Scorings: {scorings.ok} ok, {scorings.errors} failed",
    ]
    if results.noise is not None:
        lines.append(f"Noise floor: {results.noise.sigma:.3f}")
    if results.correction is not None:
        lines.append(
            f"Above chance: corrected across {results.correction.n_tests} tests "
            f"({results.correction.method}, q = {results.correction.q})"
        )
    titles = {
        OperatorFamily.DEGRADING: "Detection rate (degrading operators)",
        OperatorFamily.PRESERVING: (
            f"Spurious sensitivity (preserving operators, read against {run.alpha:.0%})"
        ),
    }
    for family, title in titles.items():
        operators = [o for o in results.operators if o.family is family]
        if operators:
            lines += ["", title]
        for operator in operators:
            crashed = f", {operator.operator_errors} operator errors"
            suffix = crashed if operator.operator_errors else ""
            lines.append("  " + _rate_line(operator.operator, operator.overall) + suffix)
            lines += [
                "    " + _rate_line(f"intensity {i.intensity:g}", i.result)
                for i in operator.by_intensity
            ]
            if operator.dose_response is not None:
                lines.append("    " + _dose_line(operator.dose_response))
    return "\n".join(lines)
