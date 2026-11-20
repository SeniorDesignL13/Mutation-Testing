"""One audit, start to finish: score a generated run and leave its results on disk.

A run's directory ends up holding ``originals.jsonl``, ``mutants.jsonl``, ``skipped.jsonl`` and
``run.json`` from the generator, ``audit.json`` (what resuming needs, see
``mtlj.engine.run_state``), ``evaluations.jsonl`` from the runner, ``results.json``, and the
run's settings (``config.toml`` and ``battery.toml``) when it was given them to save.
Results are written when a run finishes, when the judge goes down, and when the run is stopped
with Ctrl+C, SIGTERM (``docker stop``, a CI timeout) or SIGHUP (a closed terminal). A signal that
arrives while they're being written is ignored, so a paid run can't lose them at the last moment.
Only SIGKILL, a power cut or something like a full disk leaves a run without a results file. Its
``evaluations.jsonl`` still holds everything scored.

``nohup`` keeps working: a signal the process was started ignoring stays ignored.

Resuming
--------

``resume_audit`` carries on a run that stopped, however it stopped (the judge went down, a
signal, SIGKILL, a power cut), scoring only the scorings it has no row for. It reads the run's
own files, not the settings it was started from, so the run is resumed exactly as it was: the
same mutants, the same judge and the same samples. A resumed run gives the ``results.json`` an
uninterrupted one would have.

* Before anything changes or any call is made, ``prepare_resume`` checks the run: its files
  read back and agree, every stored row is one of its scorings and from its judge, no scoring
  has two rows, and the judge given is the one it was scored by. It's read-only, so a caller can
  show what's left before paying for it.
* A last line the process died while writing is cut off and scored again; a whole row that only
  lost its newline is kept, with the newline put back. Failed scorings are scored again as
  ``retry_errors`` says: ``transient`` ones by default (a timeout, a rate limit, the judge
  being down), ``all`` of them, or ``none``. Their rows move to
  ``evaluations.superseded.jsonl``, so ``evaluations.jsonl`` keeps one row per scoring and no
  paid call goes unrecorded.
* The limits default to those the run started with; a resume can change them, and its
  ``results.json`` then records the new ones.
* Only one process scores into a directory at a time (``mtlj.engine.run_state.lock_run``), so a
  resume can't run beside the run it resumes. A resume can be stopped and resumed in turn.
  ``resume_audit`` takes the lock, checks the run and resumes it. A caller that wants to show
  the plan and ask before paying (the CLI) holds the lock itself, calls ``prepare_resume``, then
  ``execute_resume`` with the plan, so nothing can change between the two.
"""

from __future__ import annotations

import logging
import signal
import threading
import tomllib
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Literal, get_args

from mtlj.engine.audit_config import SAVED_BATTERY_FILE
from mtlj.engine.generator import GenerationResult, read_generation, write_generation
from mtlj.engine.results import (
    AuditResults,
    Limits,
    build_results,
    operator_families,
    write_results,
)
from mtlj.engine.retry import PERMANENT
from mtlj.engine.run_state import (
    LOCK_FILE,
    AuditState,
    JudgeFingerprint,
    NotResumableError,
    append_superseded,
    audit_state_text,
    lock_run,
    read_audit_state,
    read_stored,
    replace_rows,
    write_audit_state,
)
from mtlj.engine.runner import (
    DEFAULT_SAMPLES,
    NO_RATE_LIMIT,
    OK,
    Evaluation,
    JsonlSink,
    RunAbortedError,
    RunLimits,
    RunSummary,
    ScoringKey,
    TaskPlan,
    build_tasks,
    check_identity,
    read_evaluations,
    resolve_limits,
    run_judge_sync,
    scoring_key,
)
from mtlj.judges.base import Judge

logger = logging.getLogger(__name__)

EVALUATIONS_FILE = "evaluations.jsonl"
INTERRUPTED = "stopped before it finished"
# Treated like Ctrl+C, so being stopped still leaves a results file.
STOP_SIGNALS = ("SIGTERM", "SIGHUP")

# Which failed scorings a resume scores again.
RetryErrors = Literal["transient", "all", "none"]
RETRY_ERRORS_MODES: tuple[str, ...] = get_args(RetryErrors)
DEFAULT_RETRY_ERRORS: RetryErrors = "transient"


def count_scorings(generation: GenerationResult, samples: int) -> int:
    """How many judge scorings a run will make, for showing before any are paid for."""
    return len(build_tasks(generation.originals, generation.mutants, samples))


def check_run_dir(run_dir: Path) -> None:
    """Refuse a directory that already holds something, so no run can overwrite another's.

    A lock file left alone (see ``mtlj.engine.run_state.lock_run``) doesn't count: it holds
    nothing, and a process that dies before writing anything else can leave one.

    Raises:
        FileExistsError: if ``run_dir`` exists and isn't an empty directory.
    """
    if run_dir.exists() and (
        not run_dir.is_dir() or any(p.name != LOCK_FILE for p in run_dir.iterdir())
    ):
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
            can't be scored, an operator with no family, a bad limit (see
            ``mtlj.engine.runner.resolve_limits``), or a judge that can't be recorded in
            ``audit.json``.
    """
    check_run_dir(run_dir)
    check_identity(judge)
    limits = resolve_limits(judge, max_concurrency, rate_limit)
    plan = build_tasks(generation.originals, generation.mutants, samples)
    operator_families(generation)
    try:
        audit_state_text(AuditState.for_run(judge, dataset="", samples=samples, limits=limits))
    except ValueError as exc:
        raise ValueError(f"judge {judge.name!r} can't be recorded in audit.json: {exc}") from exc
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
    stored = {scoring_key(e) for e in evaluations}
    return all(scoring_key(task) in stored for task in plan)


def _score_and_finish(
    generation: GenerationResult,
    judge: Judge,
    run_dir: Path,
    sink: JsonlSink,
    *,
    plan: TaskPlan,
    dataset: str,
    samples: int,
    max_concurrency: int,
    rate_limit: float,
    done: Collection[ScoringKey] = (),
    on_summary: Callable[[RunSummary], None] | None = None,
) -> AuditResults:
    """Score what ``plan`` holds less ``done`` into ``sink``, then write ``results.json``.

    ``plan`` is the whole run's, so the results are judged complete on every scoring, including
    those an earlier, stopped run stored. ``sink`` is closed here.
    """
    path = sink.path
    aborted = None
    # The runner's counts, which a run that was stopped (Ctrl+C) doesn't get back.
    summary: RunSummary | None = None
    stopper = _Stopper()
    with _handling(("SIGINT", *STOP_SIGNALS), stopper):
        try:
            with sink:
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
                        done=done,
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
        limits = resolve_limits(judge, max_concurrency, rate_limit)
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
    ``results.json``, with its status set to ``aborted``, and can be resumed with
    ``resume_audit``. ``saved_files`` (name -> text) are written to ``run_dir`` before scoring:
    the run's settings, from ``mtlj.engine.audit_config.AuditConfig.saved_files``.
    ``max_concurrency`` and ``rate_limit`` default to the judge's (see
    ``mtlj.engine.runner.resolve_limits``). ``on_summary`` is given the runner's counts, such as
    how long calls waited for the rate limit, which ``results.json`` doesn't keep; not for a run
    stopped with Ctrl+C or a signal.

    Raises:
        FileExistsError: if ``run_dir`` isn't empty (see ``check_run_dir``).
        ValueError: if the run can't start, such as a judge with no version or mutants that
            can't be scored. Nothing is written, so the same directory can be used again.
        mtlj.engine.run_state.RunInUseError: if another process took the directory first.
    """
    plan = check_can_start(
        generation,
        judge,
        run_dir,
        samples=samples,
        max_concurrency=max_concurrency,
        rate_limit=rate_limit,
    )
    limits = resolve_limits(judge, max_concurrency, rate_limit)
    with lock_run(run_dir):
        # Again, holding the lock: another run may have taken the directory since.
        check_run_dir(run_dir)
        write_generation(generation, run_dir)
        for name, text in (saved_files or {}).items():
            (run_dir / name).write_text(text, encoding="utf-8", newline="\n")
        # Last, so a directory with an audit.json has everything a resume needs.
        state = AuditState.for_run(judge, dataset=dataset, samples=samples, limits=limits)
        write_audit_state(state, run_dir)
        return _score_and_finish(
            generation,
            judge,
            run_dir,
            JsonlSink(run_dir / EVALUATIONS_FILE),
            plan=plan,
            dataset=dataset,
            samples=samples,
            max_concurrency=limits.max_concurrency,
            rate_limit=limits.rate_limit or NO_RATE_LIMIT,
            on_summary=on_summary,
        )


@dataclass(frozen=True)
class ResumePlan:
    """What resuming a run will do, worked out by ``prepare_resume`` without changing anything.

    ``kept`` are the stored rows that stay; ``retried`` the failed ones to score again.
    ``torn_tail`` is a last line the process died while writing, which is cut off;
    ``unterminated`` means the last row is whole but lost its newline, which is put back.
    """

    run_dir: Path
    state: AuditState
    generation: GenerationResult
    plan: TaskPlan
    kept: list[Evaluation]
    retried: list[Evaluation]
    torn_tail: bytes
    unterminated: bool = False

    @property
    def done(self) -> frozenset[ScoringKey]:
        return frozenset(scoring_key(e) for e in self.kept)

    @property
    def total(self) -> int:
        """Every scoring of the run."""
        return self.plan.total

    @property
    def remaining(self) -> int:
        """Scorings the resume will do: those never stored, and the failed ones retried."""
        return self.plan.total - len(self.kept)

    @property
    def needs_rewrite(self) -> bool:
        """Whether ``evaluations.jsonl`` must be rewritten before scoring can append to it."""
        return bool(self.torn_tail) or self.unterminated or bool(self.retried)


def _retry(evaluation: Evaluation, retry_errors: RetryErrors) -> bool:
    if evaluation.status == OK or retry_errors == "none":
        return False
    # A row from before error_kind was kept could be either; scoring it again is the safe side.
    return retry_errors == "all" or evaluation.error_kind != PERMANENT


def _battery_order(run_dir: Path) -> list[str] | None:
    """The battery's operator order from the saved battery file, for an older run.json."""
    path = run_dir / SAVED_BATTERY_FILE
    try:
        tables = tomllib.loads(path.read_text(encoding="utf-8")).get("operators", [])
        return [str(table["name"]) for table in tables]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare_resume(
    run_dir: Path,
    judge: Judge,
    *,
    retry_errors: RetryErrors = DEFAULT_RETRY_ERRORS,
    state: AuditState | None = None,
) -> ResumePlan:
    """Check that ``run_dir`` can be resumed with ``judge``, and work out what's left to do.

    Nothing is written. ``state`` stands in for the run's ``audit.json``, for a run made before
    runs kept one; without it, a run with no ``audit.json`` is refused.

    Raises:
        NotResumableError: if the run can't be resumed, with the reason: no run there, a run
            whose setup never finished, files that don't read back or agree, a stored row that
            isn't one of its scorings or is from another judge, a scoring stored twice, or a
            different judge than the run's.
        ValueError: for a judge with no name, model or version, or a bad ``retry_errors``.
    """
    if retry_errors not in RETRY_ERRORS_MODES:
        raise ValueError(f"retry_errors must be one of {RETRY_ERRORS_MODES}, got {retry_errors!r}")
    if not run_dir.is_dir():
        raise NotResumableError(f"{run_dir} isn't a run directory")
    check_identity(judge)
    if state is None:
        state = read_audit_state(run_dir)
    if state is None:
        raise NotResumableError(
            f"{run_dir} has no audit.json: its setup never finished, so nothing was scored. "
            "Start a new run instead."
        )

    differences = state.judge.differences(JudgeFingerprint.of(judge))
    if differences:
        raise NotResumableError(
            f"{run_dir} was scored by a different judge ({'; '.join(differences)}). Its scores "
            "can't be mixed with this judge's: start a new run instead."
        )

    try:
        generation = read_generation(run_dir, _battery_order(run_dir))
        operator_families(generation)
        plan = build_tasks(generation.originals, generation.mutants, state.samples)
    except ValueError as exc:
        raise NotResumableError(f"{run_dir} can't be resumed: {exc}") from exc

    path = run_dir / EVALUATIONS_FILE
    stored = read_stored(path)
    records = {scoring_key(task): task.record_id for task in plan}
    identity = (state.judge.name, state.judge.model, state.judge.version)
    seen: set[ScoringKey] = set()
    kept: list[Evaluation] = []
    retried: list[Evaluation] = []
    for evaluation in stored.evaluations:
        key = scoring_key(evaluation)
        what = f"{path}: {key[0]} {key[1]} sample {key[2]}"
        if records.get(key) != evaluation.record_id:
            raise NotResumableError(f"{what} isn't one of this run's scorings")
        if (evaluation.judge_name, evaluation.model, evaluation.version) != identity:
            raise NotResumableError(
                f"{what} was scored by "
                f"{(evaluation.judge_name, evaluation.model, evaluation.version)}, "
                f"not the run's judge {identity}"
            )
        if key in seen:
            raise NotResumableError(f"{what} is stored twice")
        seen.add(key)
        (retried if _retry(evaluation, retry_errors) else kept).append(evaluation)
    return ResumePlan(
        run_dir,
        state,
        generation,
        plan,
        kept,
        retried,
        stored.torn_tail,
        unterminated=stored.unterminated,
    )


def resume_limits(
    resume: ResumePlan,
    judge: Judge,
    max_concurrency: int | None = None,
    rate_limit: float | None = None,
) -> RunLimits:
    """The limits a resume runs with: those given, else the ones the run started with.

    Raises:
        ValueError: for a bad limit; a ``TypeError`` for one that isn't a number.
    """
    saved = resume.state.limits
    if max_concurrency is None:
        max_concurrency = saved.max_concurrency
    if rate_limit is None:
        rate_limit = saved.rate_limit if saved.rate_limit is not None else NO_RATE_LIMIT
    return resolve_limits(judge, max_concurrency, rate_limit)


def resume_audit(
    run_dir: Path,
    judge: Judge,
    *,
    retry_errors: RetryErrors = DEFAULT_RETRY_ERRORS,
    max_concurrency: int | None = None,
    rate_limit: float | None = None,
    state: AuditState | None = None,
    on_summary: Callable[[RunSummary], None] | None = None,
) -> AuditResults:
    """Carry on a stopped run in ``run_dir``, scoring only what it's missing.

    Takes the run's lock, checks the run (see ``prepare_resume``) and resumes it (see
    ``execute_resume``). A run with nothing left to score just gets its ``results.json`` written
    again. ``state`` is as for ``prepare_resume``.

    Raises:
        NotResumableError: if the run can't be resumed (see ``prepare_resume``). Nothing is
            changed.
        mtlj.engine.run_state.RunInUseError: if another process is scoring into ``run_dir``.
        ValueError: for a bad limit, before anything is changed.
    """
    with lock_run(run_dir):
        resume = prepare_resume(run_dir, judge, retry_errors=retry_errors, state=state)
        return execute_resume(
            resume,
            judge,
            max_concurrency=max_concurrency,
            rate_limit=rate_limit,
            on_summary=on_summary,
        )


def execute_resume(
    resume: ResumePlan,
    judge: Judge,
    *,
    max_concurrency: int | None = None,
    rate_limit: float | None = None,
    on_summary: Callable[[RunSummary], None] | None = None,
) -> AuditResults:
    """Resume the run ``resume`` describes, from ``prepare_resume`` with the same ``judge``.

    The caller must hold the run's lock (``mtlj.engine.run_state.lock_run``) from before
    ``prepare_resume`` until this returns, so the plan is still what's on disk.
    ``max_concurrency`` and ``rate_limit`` default to those the run started with (see
    ``resume_limits``). ``on_summary`` is as for ``run_audit``.

    Raises:
        ValueError: for a bad limit, before anything is changed.
    """
    limits = resume_limits(resume, judge, max_concurrency, rate_limit)
    run_dir = resume.run_dir
    path = run_dir / EVALUATIONS_FILE
    if resume.torn_tail:
        logger.warning(
            "%s ends with %d bytes of a row the process died while writing; cutting them off",
            path,
            len(resume.torn_tail),
        )
    if resume.retried:
        logger.info("scoring %d failed scorings again", len(resume.retried))
    if resume.needs_rewrite:
        # Superseded rows first: a crash between the two then keeps a row twice, never none.
        append_superseded(run_dir, resume.retried)
        replace_rows(path, resume.kept)
    logger.info(
        "resuming %s: %d of %d scorings stored, %d to go",
        run_dir,
        len(resume.kept),
        resume.total,
        resume.remaining,
    )
    sink = JsonlSink.append(path) if path.exists() else JsonlSink(path)
    return _score_and_finish(
        resume.generation,
        judge,
        run_dir,
        sink,
        plan=resume.plan,
        dataset=resume.state.dataset,
        samples=resume.state.samples,
        max_concurrency=limits.max_concurrency,
        rate_limit=limits.rate_limit or NO_RATE_LIMIT,
        done=resume.done,
        on_summary=on_summary,
    )
