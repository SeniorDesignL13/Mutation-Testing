"""One audit, start to finish: score a generated run and leave its results on disk.

A run's directory ends up holding ``originals.jsonl``, ``mutants.jsonl``, ``skipped.jsonl`` and
``run.json`` from the generator, ``evaluations.jsonl`` from the runner, ``results.json``, and
the run's settings (``config.toml`` and ``battery.toml``) when it was given them to save.
Results are written when a run finishes, when the judge goes down, and when the run is stopped
with Ctrl+C, SIGTERM (``docker stop``, a CI timeout) or SIGHUP (a closed terminal). A signal that
arrives while they're being written is ignored, so a paid run can't lose them at the last moment.
Only SIGKILL, a power cut or something like a full disk leaves a run without a results file. Its
``evaluations.jsonl`` still holds everything scored.

``nohup`` keeps working: a signal the process was started ignoring stays ignored.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

from mtlj.engine.generator import GenerationResult, write_generation
from mtlj.engine.results import (
    AuditResults,
    Limits,
    build_results,
    operator_families,
    write_results,
)
from mtlj.engine.runner import (
    DEFAULT_SAMPLES,
    Evaluation,
    JsonlSink,
    RunAbortedError,
    RunSummary,
    TaskPlan,
    build_tasks,
    check_identity,
    read_evaluations,
    resolve_limits,
    run_judge_sync,
)
from mtlj.judges.base import Judge

logger = logging.getLogger(__name__)

EVALUATIONS_FILE = "evaluations.jsonl"
INTERRUPTED = "stopped before it finished"
# Treated like Ctrl+C, so being stopped still leaves a results file.
STOP_SIGNALS = ("SIGTERM", "SIGHUP")


def count_scorings(generation: GenerationResult, samples: int) -> int:
    """How many judge scorings a run will make, for showing before any are paid for."""
    return len(build_tasks(generation.originals, generation.mutants, samples))


def check_run_dir(run_dir: Path) -> None:
    """Refuse a directory that already holds something, so no run can overwrite another's.

    Raises:
        FileExistsError: if ``run_dir`` exists and isn't an empty directory.
    """
    if run_dir.exists() and (not run_dir.is_dir() or any(run_dir.iterdir())):
        raise FileExistsError(f"{run_dir} already exists and isn't empty")


def check_can_start(
    generation: GenerationResult,
    judge: Judge,
    run_dir: Path,
    *,
    samples: int = DEFAULT_SAMPLES,
    max_concurrency: int | None = None,
    rate_limit: float | None = None,
) -> TaskPlan:
    """Everything run_audit checks before it writes anything or calls the judge.

    Returns the run's scorings, so a caller can show what it will cost first.

    Raises:
        FileExistsError: if ``run_dir`` isn't empty (see ``check_run_dir``).
        ValueError: if the run can't start, such as a judge with no version, mutants that
            can't be scored, an operator with no family, or a bad limit (see
            ``mtlj.engine.runner.resolve_limits``).
    """
    check_run_dir(run_dir)
    check_identity(judge)
    resolve_limits(judge, max_concurrency, rate_limit)
    plan = build_tasks(generation.originals, generation.mutants, samples)
    operator_families(generation)
    return plan


@contextmanager
def _handling(names: tuple[str, ...], handler: Callable[..., Any] | int) -> Iterator[None]:
    """Run a block with ``handler`` on the signals called ``names``, where that's safe.

    Signals left alone: one the process inherited as ignored, which is how ``nohup`` works, and
    one whose handler wasn't installed from Python, since it couldn't be put back. Python only
    allows handlers on the main thread, and SIGHUP doesn't exist on Windows.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {}
    for signum in (getattr(signal, name) for name in names if hasattr(signal, name)):
        current = signal.getsignal(signum)
        if current is not None and current != signal.SIG_IGN:
            previous[signum] = current
            signal.signal(signum, handler)
    try:
        yield
    finally:
        for signum, current in previous.items():
            signal.signal(signum, current)


class _Stopper:
    """Stops scoring on a signal, and ignores the signal once scoring is over.

    It's installed once, before scoring starts, and stays until the results are written, so
    there's no moment where a signal finds its default handler and ends the process between the
    two. It raises ``KeyboardInterrupt`` only while ``scoring`` is set, which run_audit does
    inside the ``try`` that catches it.
    """

    def __init__(self) -> None:
        self.scoring = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        if self.scoring:
            raise KeyboardInterrupt


def _stored_evaluations(path: Path) -> list[Evaluation]:
    # A run stopped before its first result leaves no file (see JsonlSink).
    return read_evaluations(path) if path.exists() else []


def _every_scoring_stored(plan: TaskPlan, evaluations: list[Evaluation]) -> bool:
    """Whether every planned scoring has its row, failed or not: the run got to the end.

    A stop that lands after the last scoring returns but before run_audit notices still raises
    ``KeyboardInterrupt``. Checking what was stored, not when the signal came, keeps such a run
    complete instead of throwing away results that were all paid for.
    """
    stored = {(e.target_id, e.sample_index) for e in evaluations}
    return all((task.target_id, task.sample_index) in stored for task in plan)


def run_audit(
    generation: GenerationResult,
    judge: Judge,
    run_dir: Path,
    *,
    dataset: str,
    samples: int = DEFAULT_SAMPLES,
    max_concurrency: int | None = None,
    rate_limit: float | None = None,
    saved_files: Mapping[str, str] | None = None,
    on_summary: Callable[[RunSummary], None] | None = None,
) -> AuditResults:
    """Score a generated run with ``judge`` and store everything in ``run_dir``.

    A run that stops early, because the judge is down or it was stopped, still gets a
    ``results.json``, with its status set to ``aborted``. ``saved_files`` (name -> text) are
    written to ``run_dir`` before scoring: the run's settings, from
    ``mtlj.engine.audit_config.AuditConfig.saved_files``. ``max_concurrency`` and
    ``rate_limit`` default to the judge's (see ``mtlj.engine.runner.resolve_limits``).
    ``on_summary`` is given the runner's counts, such as how long calls waited for the rate
    limit, which ``results.json`` doesn't keep; not for a run stopped with Ctrl+C or a signal.

    Raises:
        FileExistsError: if ``run_dir`` isn't empty (see ``check_run_dir``).
        ValueError: if the run can't start, such as a judge with no version or mutants that
            can't be scored. Nothing is written, so the same directory can be used again.
    """
    plan = check_can_start(
        generation,
        judge,
        run_dir,
        samples=samples,
        max_concurrency=max_concurrency,
        rate_limit=rate_limit,
    )
    write_generation(generation, run_dir)
    for name, text in (saved_files or {}).items():
        (run_dir / name).write_text(text, encoding="utf-8", newline="\n")
    path = run_dir / EVALUATIONS_FILE
    # Already checked by check_can_start; resolved again for the results.
    limits = resolve_limits(judge, max_concurrency, rate_limit)

    aborted = None
    # The runner's counts, which a run that was stopped (Ctrl+C) doesn't get back.
    summary: RunSummary | None = None
    stopper = _Stopper()
    with _handling(("SIGINT", *STOP_SIGNALS), stopper):
        try:
            with JsonlSink(path) as sink:
                stopper.scoring = True
                try:
                    summary = run_judge_sync(
                        judge,
                        generation.originals,
                        generation.mutants,
                        sink,
                        samples=samples,
                        max_concurrency=max_concurrency,
                        rate_limit=rate_limit,
                    )
                finally:
                    stopper.scoring = False
        except RunAbortedError as exc:
            logger.error("run stopped early: %s", exc)
            aborted = str(exc)
            summary = exc.summary
        except KeyboardInterrupt:
            aborted = INTERRUPTED

        evaluations = _stored_evaluations(path)
        if aborted == INTERRUPTED and _every_scoring_stored(plan, evaluations):
            logger.warning("stopped after the last scoring, so the run is complete")
            aborted = None
        elif aborted == INTERRUPTED:
            logger.error("run stopped")
        results = build_results(
            generation,
            evaluations,
            judge=judge,
            dataset=dataset,
            samples=samples,
            aborted=aborted,
            limits=Limits(max_concurrency=limits.max_concurrency, rate_limit=limits.rate_limit),
        )
        write_results(results, run_dir)
    if summary is not None and on_summary is not None:
        on_summary(summary)
    return results
