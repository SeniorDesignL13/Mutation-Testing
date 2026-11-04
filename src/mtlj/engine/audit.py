"""One audit, start to finish: score a generated run and leave its results on disk.

A run's directory ends up holding ``originals.jsonl``, ``mutants.jsonl``, ``skipped.jsonl`` and
``run.json`` from the generator, ``evaluations.jsonl`` from the runner, and ``results.json``.
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

from mtlj.engine.generator import GenerationResult, write_generation
from mtlj.engine.results import AuditResults, build_results, operator_families, write_results
from mtlj.engine.runner import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_SAMPLES,
    Evaluation,
    JsonlSink,
    RunAbortedError,
    build_tasks,
    check_identity,
    read_evaluations,
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


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def _stored_evaluations(path: Path) -> list[Evaluation]:
    # A run stopped before its first result leaves no file (see JsonlSink).
    return read_evaluations(path) if path.exists() else []


def run_audit(
    generation: GenerationResult,
    judge: Judge,
    run_dir: Path,
    *,
    dataset: str,
    samples: int = DEFAULT_SAMPLES,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> AuditResults:
    """Score a generated run with ``judge`` and store everything in ``run_dir``.

    A run that stops early, because the judge is down or it was stopped, still gets a
    ``results.json``, with its status set to ``aborted``.

    Raises:
        FileExistsError: if ``run_dir`` isn't empty (see ``check_run_dir``).
        ValueError: if the run can't start, such as a judge with no version or mutants that
            can't be scored. Nothing is written, so the same directory can be used again.
    """
    check_run_dir(run_dir)
    check_identity(judge)
    if max_concurrency < 1:
        raise ValueError(f"max_concurrency must be at least 1, got {max_concurrency}")
    build_tasks(generation.originals, generation.mutants, samples)
    operator_families(generation)
    write_generation(generation, run_dir)
    path = run_dir / EVALUATIONS_FILE

    aborted = None
    try:
        with _handling(STOP_SIGNALS, _interrupt), JsonlSink(path) as sink:
            run_judge_sync(
                judge,
                generation.originals,
                generation.mutants,
                sink,
                samples=samples,
                max_concurrency=max_concurrency,
            )
    except RunAbortedError as exc:
        logger.error("run stopped early: %s", exc)
        aborted = str(exc)
    except KeyboardInterrupt:
        logger.error("run stopped")
        aborted = INTERRUPTED

    with _handling(("SIGINT", *STOP_SIGNALS), signal.SIG_IGN):
        results = build_results(
            generation,
            _stored_evaluations(path),
            judge=judge,
            dataset=dataset,
            samples=samples,
            aborted=aborted,
        )
        write_results(results, run_dir)
    return results
