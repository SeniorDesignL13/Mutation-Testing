"""The audit result artifact: one structured file every consumer of a run reads.

``build_results`` turns a run's mutants and evaluations into ``AuditResults``, and
``write_results`` stores them as ``results.json`` in the run's directory, next to the raw
``evaluations.jsonl``. docs/results.md documents the format. It holds no timestamps, so the
same run always gives a byte-identical file.

Each operator gets the headline metric of its family (docs/metrics.md): detection rate for
degrading operators, spurious sensitivity for preserving ones. Each is reported pooled across
the operator's intensities and at each intensity. A record appears once per intensity in the
pooled figure, and those mutants share one original, so one unlucky draw can move them together.
Its interval is therefore narrower than independent mutants would give, and the binomial test,
which assumes independence, is left out of it.

A file is only ever written with metrics for a finished run. An aborted run's rates would mix
scored mutants with ones that were never reached, so ``operators`` stays empty and the rows
that were scored stay in ``evaluations.jsonl``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from mtlj.engine.generator import NO_CHANGE, OPERATOR_ERROR, GenerationResult
from mtlj.engine.runner import OK, ORIGINAL, Evaluation
from mtlj.judges.base import Judge
from mtlj.operators.base import OperatorFamily
from mtlj.stats.detection import (
    CI_LEVEL,
    DEFAULT_ALPHA,
    DetectionRate,
    MutantScores,
    detection_rate,
    noise_floor,
)
from mtlj.stats.paired import effect_size, paired_delta
from mtlj.stats.spurious import SpuriousSensitivity, spurious_sensitivity
from mtlj.stats.variance import Condition, condition_variances

# Bump on any change to the format. read_results rejects every other version.
SCHEMA_VERSION = 1
# The file a run's results are saved as, in the run's directory.
RESULTS_FILE = "results.json"

COMPLETE = "complete"
ABORTED = "aborted"  # The run stopped early.
INSUFFICIENT_DATA = "insufficient_data"  # No original has the 2+ scores that measure noise.

Status = Literal["complete", "aborted", "insufficient_data"]
Interval = tuple[float, float]


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


class _Rate(_Model):
    """What every rate reports. ``rate`` and ``ci`` are None when no mutant is testable."""

    rate: float | None
    ci: Interval | None
    testable: int
    excluded: dict[str, int]
    tau: float
    n_samples: int
    paired_delta: PairedDeltaResult
    effect_size: float | None


class DetectionResult(_Rate):
    """Detection rate of a degrading operator's mutants."""

    metric: Literal["detection_rate"] = "detection_rate"
    detected: int


class SpuriousResult(_Rate):
    """Spurious sensitivity of a preserving operator's mutants, read against alpha.

    ``p_value`` and ``above_chance`` are None when the test wasn't run: when no mutant is
    testable, and when pooled across intensities, since the test assumes independent mutants.
    """

    metric: Literal["spurious_sensitivity"] = "spurious_sensitivity"
    flagged: int
    penalized: int
    rewarded: int
    p_value: float | None
    above_chance: bool | None


class IntensityResult(_Model):
    """One operator's result at one intensity."""

    intensity: float
    result: DetectionResult | SpuriousResult


class OperatorResult(_Model):
    """One operator's result, pooled across its intensities and at each one.

    ``operator_errors`` counts mutants the operator crashed on. They're in no rate's denominator.
    """

    operator: str
    family: OperatorFamily
    operator_errors: int
    overall: DetectionResult | SpuriousResult
    by_intensity: list[IntensityResult]


class AuditResults(_Model):
    """A run's results. ``noise`` and ``variance`` are None, and ``operators`` empty, unless the
    run's status is ``complete``."""

    schema_version: int = SCHEMA_VERSION
    run: RunInfo
    noise: Noise | None
    operators: list[OperatorResult]
    variance: dict[str, Any] | None


def _common_fields(rate: DetectionRate | SpuriousSensitivity) -> dict[str, Any]:
    delta = paired_delta(rate.outcomes)
    return {
        "rate": rate.rate,
        "ci": rate.ci,
        "testable": rate.testable,
        "excluded": {reason.value: count for reason, count in rate.excluded.items()},
        "tau": rate.tau,
        "n_samples": rate.n_samples,
        "paired_delta": PairedDeltaResult(mean=delta.mean, ci=delta.ci, sd=delta.sd, n=delta.n),
        "effect_size": effect_size(delta, rate.sigma),
    }


def _score(
    family: OperatorFamily,
    originals: dict[str, list[float]],
    mutants: Sequence[MutantScores],
    alpha: float,
    *,
    pooled: bool = False,
) -> DetectionResult | SpuriousResult:
    """Score one group of mutants with its family's headline metric.

    ``pooled`` means the group spans several intensities, so its mutants aren't independent.
    """
    if family is OperatorFamily.DEGRADING:
        detection = detection_rate(originals, mutants, alpha)
        return DetectionResult(**_common_fields(detection), detected=detection.detected)
    spurious = spurious_sensitivity(originals, mutants, alpha)
    tested = spurious.p_value is not None and not pooled
    return SpuriousResult(
        **_common_fields(spurious),
        flagged=spurious.flagged,
        penalized=spurious.penalized,
        rewarded=spurious.rewarded,
        p_value=spurious.p_value if tested else None,
        above_chance=spurious.above_chance if tested else None,
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
) -> list[OperatorResult]:
    groups = _group_mutants(generation, scores)
    families = operator_families(generation)
    crashes = Counter(s.operator_name for s in generation.skipped if s.reason == OPERATOR_ERROR)
    results = []
    for operator in sorted({name for name, _ in groups}):
        family = families[operator]
        intensities = sorted(i for name, i in groups if name == operator)
        pooled = [m for intensity in intensities for m in groups[(operator, intensity)]]
        results.append(
            OperatorResult(
                operator=operator,
                family=family,
                operator_errors=crashes[operator],
                overall=_score(family, originals, pooled, alpha, pooled=len(intensities) > 1),
                by_intensity=[
                    IntensityResult(
                        intensity=intensity,
                        result=_score(family, originals, groups[(operator, intensity)], alpha),
                    )
                    for intensity in intensities
                ],
            )
        )
    return results


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
        return AuditResults(run=run, noise=None, operators=[], variance=None)
    return AuditResults(
        run=run,
        noise=Noise(sigma=noise_floor(originals.values()), n_originals=measurable),
        operators=_operator_results(generation, originals, scores, alpha),
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


def _interval(ci: Interval | None) -> str:
    return "" if ci is None else f" [{ci[0]:.1%}, {ci[1]:.1%}]"


def _rate_line(label: str, result: DetectionResult | SpuriousResult) -> str:
    rate = f"{_percent(result.rate)}{_interval(result.ci)}"
    mean = result.paired_delta.mean
    delta = "" if mean is None else f", mean drop {mean:+.3f}"
    if isinstance(result, DetectionResult):
        counts = f"{result.detected}/{result.testable} detected"
    else:
        counts = (
            f"{result.flagged}/{result.testable} flagged "
            f"({result.penalized} penalized, {result.rewarded} rewarded)"
        )
        if result.above_chance:
            counts += ", above chance"
    excluded = {reason: n for reason, n in result.excluded.items() if n}
    skipped = f", excluded {excluded}" if excluded else ""
    return f"{label}: {rate}, {counts}{delta}{skipped}"


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
    return "\n".join(lines)
