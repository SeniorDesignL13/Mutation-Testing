"""Judge runner: score originals and mutants with one judge, many calls at a time.

Every original and every mutant is scored ``samples`` times, so detection rate can pair each
mutant with its own original and measure the judge's noise (see docs/metrics.md).

* Judges are synchronous (``Judge.__call__``), so each call runs on a worker thread while asyncio
  keeps up to ``max_concurrency`` of them in flight. The adapter contract
  (tests/judges/test_contract.py, ``check_concurrent_calls``) guarantees adapters are safe to
  call from several threads at once. A thread can't be interrupted, so adapters must put a
  timeout on their backend calls: the runner can't stop a call that hangs.
* Each result goes to a ``ResultSink`` as soon as its call finishes, not at the end, so a run
  that dies part way keeps everything scored so far. ``JsonlSink`` writes one line per scoring.
* A judge call that raises is recorded as an ``error`` evaluation, with the exception, and the
  run carries on. Only when ``max_consecutive_errors`` calls in a row fail (a revoked key, a
  missing model) does the run stop, with ``RunAbortedError``, rather than fail through the
  corpus. Calls already in flight still finish and are written first, since they're paid for.
  Error messages are stored as valid UTF-8, at most ``MAX_ERROR_CHARS`` long.
* A sink that raises stops the run, and nothing more is written to it: a sink that failed
  part way through a write could be corrupted by the next one. Anything else that goes wrong
  outside the judge call stops the run the same way.
* A run that stops early raises with its ``RunSummary`` attached, so the caller can tell how
  far it got (``RunAbortedError.summary``, or a note on the exception). A run whose last
  calls fail but that scored every task still completes normally.
* The judge's name, model and version must be non-empty strings; that's checked before any
  call, so a misconfigured adapter costs nothing.
* Every sample is stored on its own, with the verdict counts and claim count the judge reported
  (the 2026-10-20 decision). Nothing is averaged on write.
* The judge sees the record's question as ``input``, the answer (original or mutated) as
  ``output``, and ``{"passages": [{"passage_id", "text", "title"}, ...]}`` as ``context``: the
  record's passages, in order. This is the shape the adapter contract tests
  (tests/judges/test_contract.py, ``PASSAGES``).

Not here yet: retries (MTLJ-94), rate limiting (MTLJ-96), resuming a run (MTLJ-91), the
response cache (MTLJ-93) and a Postgres sink (MTLJ-26/28).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord, Passage
from mtlj.judges.base import Context, Judge

logger = logging.getLogger(__name__)

# What an evaluation scored.
ORIGINAL = "original"
MUTANT = "mutant"

# How a scoring ended.
OK = "ok"
ERROR = "error"

# Every text is scored 5 times by default (docs/metrics.md, "Decisions", 2026-10-20).
DEFAULT_SAMPLES = 5
# The spike ran 4 calls at a time on gpt-4o-mini without hitting rate limits.
DEFAULT_MAX_CONCURRENCY = 4
# This many failed calls in a row means the judge is down, not that some calls were unlucky.
DEFAULT_MAX_CONSECUTIVE_ERRORS = 20
# Longest error message stored; backend errors can carry a whole response body.
MAX_ERROR_CHARS = 1000

# The key the record's passages are passed under in a judge's context.
PASSAGES = "passages"


def judge_context(passages: Sequence[Passage]) -> Context:
    """The context a judge is given: the record's passages as plain dicts, in order.

    Each call gets new dicts, so a judge that edits its context can't affect other calls.
    """
    return {PASSAGES: [p.model_dump() for p in passages]}


class RunAbortedError(RuntimeError):
    """Too many judge calls in a row failed, so the run stopped early.

    ``summary`` holds the counts up to the stop.
    """

    def __init__(self, message: str, summary: RunSummary) -> None:
        super().__init__(message)
        self.summary = summary

    def __reduce__(self) -> tuple[Any, ...]:
        # So it survives pickling (e.g. from a worker process) with its summary.
        return (type(self), (self.args[0], self.summary))


@dataclass(frozen=True, slots=True)
class EvalTask:
    """One scoring to do: one text, one sample."""

    kind: str  # ORIGINAL or MUTANT
    target_id: str  # the record_id for an original, the mutant_id for a mutant
    record_id: str
    sample_index: int
    input: str
    output: str
    passages: tuple[Passage, ...]
    operator_name: str | None = None
    intensity: float | None = None


class Evaluation(BaseModel):
    """One scoring's result, as it is stored.

    ``score`` is normalized to [0, 1] with higher = better; ``raw_score`` is on the judge's own
    range. Both are None, and ``error_type`` and ``error`` say why, when the call failed. The
    verdict counts and ``n_claims`` are None when the judge doesn't report them.
    """

    model_config = {"extra": "forbid", "frozen": True}

    kind: Literal["original", "mutant"]
    target_id: str
    record_id: str
    sample_index: int
    judge_name: str
    model: str
    version: str
    status: Literal["ok", "error"]
    latency_ms: float
    operator_name: str | None = None
    intensity: float | None = None
    score: float | None = None
    raw_score: float | None = None
    verdict_yes: int | None = None
    verdict_borderline: int | None = None
    verdict_no: int | None = None
    n_claims: int | None = None
    error_type: str | None = None
    error: str | None = None


@dataclass
class RunSummary:
    """Counts for a quick look at a run."""

    judge_name: str
    model: str
    version: str
    samples: int
    total: int = 0
    ok: int = 0
    errors: int = 0
    errors_by_type: dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0


class ResultSink(Protocol):
    """Where the runner sends each evaluation as it completes."""

    async def write(self, evaluation: Evaluation) -> None: ...


class MemorySink:
    """Keeps evaluations in a list. For tests and small runs."""

    def __init__(self) -> None:
        self.evaluations: list[Evaluation] = []

    async def write(self, evaluation: Evaluation) -> None:
        self.evaluations.append(evaluation)


class JsonlSink:
    """Appends one JSON line per evaluation to ``path``, flushed as it's written.

    A run that dies part way leaves every line written so far. The file is created when the
    sink is, and must not exist yet, so a new run can't overwrite an old one's results and two
    runs can't share a file. Closing a sink nothing was written to removes the file, so a run
    rejected before any call doesn't block its retry.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # "x" fails if the file exists. newline="\n" keeps files identical on Windows and Linux.
        self._file = path.open("x", encoding="utf-8", newline="\n")
        self._written = False

    async def write(self, evaluation: Evaluation) -> None:
        line = evaluation.model_dump_json() + "\n"
        # Set before writing: a write that fails part way may still have put bytes in the
        # file, and close() must not delete those.
        self._written = True
        # One write without awaiting, so lines from concurrent workers never interleave.
        self._file.write(line)
        self._file.flush()

    def close(self) -> None:
        if self._file.closed:
            return
        self._file.close()
        if not self._written:
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def read_evaluations(path: Path) -> list[Evaluation]:
    """Read back the evaluations a ``JsonlSink`` wrote.

    A last line without its newline is one the process died while writing; it is skipped with
    a warning, so the rest of the run can still be read. Every other line is validated, so a
    wrong type or an unknown field raises ``pydantic.ValidationError``.
    """
    evaluations = []
    with path.open(encoding="utf-8", newline="\n") as lines:
        for line in lines:
            if not line.endswith("\n"):
                logger.warning("skipped a partly written last line in %s", path)
                break
            if line.strip():
                evaluations.append(Evaluation.model_validate_json(line))
    return evaluations


class TaskPlan:
    """Every scoring a run will do, made one at a time as it's needed.

    Its length is the number of scorings. Iterating it more than once gives the same tasks.
    """

    def __init__(
        self, records: dict[str, DatasetRecord], mutants: list[Mutant], samples: int
    ) -> None:
        self._records = records
        self._mutants = mutants
        self._samples = samples
        self._passages = {rid: tuple(r.context) for rid, r in records.items()}

    def __len__(self) -> int:
        return self._samples * (len(self._records) + len(self._mutants))

    def __iter__(self) -> Iterator[EvalTask]:
        # Sample by sample, so a run cut short still has some scores for every text.
        for sample_index in range(self._samples):
            for record in self._records.values():
                yield EvalTask(
                    kind=ORIGINAL,
                    target_id=record.record_id,
                    record_id=record.record_id,
                    sample_index=sample_index,
                    input=record.question,
                    output=record.answer,
                    passages=self._passages[record.record_id],
                )
            for mutant in self._mutants:
                yield EvalTask(
                    kind=MUTANT,
                    target_id=mutant.mutant_id,
                    record_id=mutant.record_id,
                    sample_index=sample_index,
                    input=self._records[mutant.record_id].question,
                    output=mutant.mutated_text,
                    passages=self._passages[mutant.record_id],
                    operator_name=mutant.operator_name,
                    intensity=mutant.intensity,
                )


def build_tasks(
    records: Iterable[DatasetRecord], mutants: Iterable[Mutant], samples: int
) -> TaskPlan:
    """Every original and every mutant, ``samples`` times each, checked before any is made.

    Ordered sample by sample (every text's first sample, then every text's second, ...).

    Raises:
        ValueError: if ``samples`` < 1, two records or two mutants share an id, or a mutant's
            record is missing or has changed since the mutant was made.
    """
    if samples < 1:
        raise ValueError(f"samples must be at least 1, got {samples}")

    by_id: dict[str, DatasetRecord] = {}
    for record in records:
        if record.record_id in by_id:
            raise ValueError(f"duplicate record_id {record.record_id!r}")
        by_id[record.record_id] = record

    mutants = list(mutants)
    mutant_ids: set[str] = set()
    for mutant in mutants:
        if mutant.mutant_id in mutant_ids:
            raise ValueError(f"duplicate mutant_id {mutant.mutant_id!r}")
        mutant_ids.add(mutant.mutant_id)
        record = by_id.get(mutant.record_id)
        if record is None:
            raise ValueError(
                f"mutant {mutant.mutant_id!r}'s record {mutant.record_id!r} is missing"
            )
        # Scoring a mutant against a different answer or context would break the pairing.
        mutant.check_record(record)

    return TaskPlan(by_id, mutants, samples)


def _error_message(exc: Exception) -> str:
    """The exception's message, safe to store: valid UTF-8 and at most MAX_ERROR_CHARS long.

    Backend errors can carry undecodable bytes or a whole response body; either would make
    the row fail to write, and the runner would treat that as a sink failure.
    """
    message = str(exc).encode("utf-8", "backslashreplace").decode("utf-8")
    if len(message) > MAX_ERROR_CHARS:
        message = message[:MAX_ERROR_CHARS] + "... (truncated)"
    return message


def _check_identity(judge: Judge) -> None:
    """Reject a judge whose name, model or version isn't a non-empty string, before any call."""
    for attr in ("name", "model", "version"):
        value = getattr(judge, attr, None)
        if not isinstance(value, str) or not value:
            raise ValueError(f"judge.{attr} must be a non-empty string, got {value!r}")


async def _evaluate(judge: Judge, task: EvalTask, executor: ThreadPoolExecutor) -> Evaluation:
    """Score one task on a worker thread. A failed call becomes an ERROR evaluation."""
    loop = asyncio.get_running_loop()
    # A fresh context per call, so a judge that edits it can't affect other calls.
    context = judge_context(task.passages)
    provenance = {
        "kind": task.kind,
        "target_id": task.target_id,
        "record_id": task.record_id,
        "sample_index": task.sample_index,
        "operator_name": task.operator_name,
        "intensity": task.intensity,
        # Judge.__call__ copies these from the judge into every result, so OK and ERROR rows
        # always agree.
        "judge_name": judge.name,
        "model": judge.model,
        "version": judge.version,
    }
    start = time.perf_counter()
    try:
        result = await loop.run_in_executor(executor, judge, task.input, task.output, context)
    except Exception as exc:  # one failed call must not stop the whole run
        message = _error_message(exc)
        logger.warning(
            "judge %s failed on %s %s (sample %d): %s: %s",
            judge.name,
            task.kind,
            task.target_id,
            task.sample_index,
            type(exc).__name__,
            message,
        )
        return Evaluation(
            **provenance,
            status=ERROR,
            latency_ms=(time.perf_counter() - start) * 1000,
            error_type=type(exc).__name__,
            error=message,
        )

    counts = result.verdict_counts
    return Evaluation(
        **provenance,
        status=OK,
        latency_ms=(time.perf_counter() - start) * 1000,
        score=result.score,
        raw_score=result.raw_score,
        verdict_yes=counts.yes if counts else None,
        verdict_borderline=counts.borderline if counts else None,
        verdict_no=counts.no if counts else None,
        n_claims=result.n_claims,
    )


async def run_judge(
    judge: Judge,
    records: Iterable[DatasetRecord],
    mutants: Iterable[Mutant],
    sink: ResultSink,
    *,
    samples: int = DEFAULT_SAMPLES,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_consecutive_errors: int | None = DEFAULT_MAX_CONSECUTIVE_ERRORS,
) -> RunSummary:
    """Score every original and mutant ``samples`` times with ``judge``, concurrently.

    Each evaluation is written to ``sink`` as soon as its call finishes. A failed judge call is
    written as an ``error`` evaluation and the run continues.

    Args:
        judge: any adapter; it must pass the adapter contract.
        records: the originals. Every record is scored, even one with no mutants, since all
            originals feed the noise floor.
        mutants: the mutants to score. Each one's record must be in ``records``.
        sink: where evaluations go.
        samples: how many times each text is scored.
        max_concurrency: the most judge calls in flight at once.
        max_consecutive_errors: stop the run once this many calls in a row have failed.
            ``None`` never stops.

    Raises:
        ValueError: for bad arguments (see ``build_tasks``), or a judge whose name, model or
            version isn't a non-empty string, before any call is made.
        RunAbortedError: once ``max_consecutive_errors`` calls in a row have failed. Every
            result, failures included, has been written by then. Its ``summary`` has the counts.
        Exception: whatever ``sink.write`` raised first (or anything else that went wrong
            outside the judge call), with the run's summary as a note. Nothing is written after.
    """
    if max_concurrency < 1:
        raise ValueError(f"max_concurrency must be at least 1, got {max_concurrency}")
    if max_consecutive_errors is not None and max_consecutive_errors < 1:
        raise ValueError(f"max_consecutive_errors must be at least 1, got {max_consecutive_errors}")
    _check_identity(judge)
    tasks = build_tasks(records, mutants, samples)
    summary = RunSummary(judge.name, judge.model, judge.version, samples)
    if not tasks:
        return summary

    logger.info(
        "scoring %d texts x %d samples with %s (%s), %d at a time",
        len(tasks) // samples,
        samples,
        judge.name,
        judge.model,
        max_concurrency,
    )
    started = time.perf_counter()
    consecutive_errors = 0
    # Why the run is stopping early, if it is. Workers take no new task once either is set.
    # After an abort, calls already in flight are still written (they're paid for). After a
    # failure (the sink, or anything else outside the judge call) nothing is: a sink that
    # failed mid-write could be corrupted further.
    failure: Exception | None = None
    abort_reason: str | None = None
    # Workers take tasks from one shared, lazy iterator. Only the event loop's thread touches
    # it, so no lock is needed, and only the tasks in flight exist at any time.
    pending = iter(tasks)
    # Its own pool, sized to max_concurrency: the default executor has a fixed size that
    # would cap concurrency without saying so.
    executor = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="judge")
    # One write at a time: a sink that awaits inside write() (e.g. a database connection)
    # mustn't see overlapping writes, or a write that starts after another one failed.
    write_lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal consecutive_errors, failure, abort_reason
        for task in pending:
            if failure is not None or abort_reason is not None:
                return
            try:
                evaluation = await _evaluate(judge, task, executor)
                async with write_lock:
                    if failure is not None:
                        return
                    await sink.write(evaluation)
            except Exception as exc:  # judge errors are rows already; this is the runner's own
                failure = failure or exc
                return
            summary.total += 1
            if evaluation.status == OK:
                summary.ok += 1
                consecutive_errors = 0
                continue
            summary.errors += 1
            error_type = evaluation.error_type or ""
            summary.errors_by_type[error_type] = summary.errors_by_type.get(error_type, 0) + 1
            consecutive_errors += 1
            if (
                max_consecutive_errors is not None
                and consecutive_errors >= max_consecutive_errors
                and abort_reason is None
            ):
                abort_reason = (
                    f"{consecutive_errors} judge calls in a row failed; last: "
                    f"{evaluation.error_type}: {evaluation.error}"
                )

    workers = [asyncio.create_task(worker()) for _ in range(min(max_concurrency, len(tasks)))]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        # Cancelled from outside (e.g. Ctrl-C): stop the workers before re-raising.
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    finally:
        # Drop calls that haven't started. A call already running on a thread can't be
        # interrupted, so after a cancel the process still waits for it (at most
        # max_concurrency calls) before it exits; its result isn't written.
        executor.shutdown(wait=False, cancel_futures=True)

    summary.elapsed_s = time.perf_counter() - started
    if failure is not None:
        logger.error("run with %s stopped: %r: %s", judge.name, failure, summary)
        failure.add_note(f"run summary when it stopped: {summary}")
        raise failure
    # The limit can be reached on the last calls, with nothing left to skip: that run is complete.
    if abort_reason is not None and summary.total < len(tasks):
        logger.error("run with %s stopped early: %s", judge.name, summary)
        raise RunAbortedError(abort_reason, summary)
    logger.info("scored with %s: %s", judge.name, summary)
    return summary


def run_judge_sync(
    judge: Judge,
    records: Iterable[DatasetRecord],
    mutants: Iterable[Mutant],
    sink: ResultSink,
    *,
    samples: int = DEFAULT_SAMPLES,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_consecutive_errors: int | None = DEFAULT_MAX_CONSECUTIVE_ERRORS,
) -> RunSummary:
    """``run_judge`` for code that isn't async, such as the CLI."""
    return asyncio.run(
        run_judge(
            judge,
            records,
            mutants,
            sink,
            samples=samples,
            max_concurrency=max_concurrency,
            max_consecutive_errors=max_consecutive_errors,
        )
    )
