"""What a run keeps on disk so it can be resumed, and the file handling resuming needs.

* ``audit.json`` (``AuditState``) holds what a run can't be resumed without and its other files
  don't say: the dataset label and samples ``results.json`` is built with, the judge it was
  scored by, and its limits. ``run_audit`` writes it once the run's other files are written and
  before the first judge call, so a directory without one never got as far as scoring.
* A judge is compared on everything ``mtlj.engine.cache`` keys it on: class, name, model,
  version, score range, direction, and its ``cache_settings()`` when they're JSON. Scores from
  two different judges can't be mixed in one run.
* ``lock_run`` makes sure only one process scores into a run directory at a time. The lock is
  the operating system's (``filelock``), so a process that dies, even with SIGKILL, releases
  it. The ``.lock`` file it uses stays in the directory; it holds nothing.
* ``read_stored`` reads ``evaluations.jsonl`` the way a resume needs it: a last line without
  its newline is one the process died while writing, and is reported rather than read.
  ``replace_rows`` rewrites the file atomically, so a crash leaves the old file or the new one.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError

from mtlj.engine.cache import judge_core_identity, settings_sha256
from mtlj.engine.runner import Evaluation, RunLimits
from mtlj.judges.base import Judge

logger = logging.getLogger(__name__)

AUDIT_STATE_FILE = "audit.json"
LOCK_FILE = ".lock"
# Failed rows a resume scored again, moved here from evaluations.jsonl so no paid call is lost.
SUPERSEDED_FILE = "evaluations.superseded.jsonl"
# audit.json's format. read_audit_state refuses every other one.
STATE_SCHEMA_VERSION = 1


class NotResumableError(ValueError):
    """A run directory can't be resumed: what it holds is missing, broken or from elsewhere."""


class RunInUseError(RuntimeError):
    """Another process is scoring into the run directory."""


class JudgeFingerprint(BaseModel):
    """What decides a judge's scores, as ``mtlj.engine.cache`` keys them, safe to store.

    The judge's ``cache_settings()`` are kept only as a SHA-256 of their JSON: a judge's public
    attributes can hold an API key or a token in a URL, and a run directory gets shared.
    ``settings_sha256`` is None for a judge whose settings aren't JSON, such as a
    ``CallableJudge``. Any field that's None on either side is left out of the comparison; a
    run made before runs kept a fingerprint only has the name, model and version its rows hold
    (see ``unverified``).
    """

    model_config = {"extra": "ignore", "frozen": True}

    judge_class: str | None
    name: str
    model: str
    version: str
    score_range: tuple[float, float] | None
    higher_is_better: bool | None
    settings_sha256: str | None

    @classmethod
    def of(cls, judge: Judge) -> JudgeFingerprint:
        core = judge_core_identity(judge)
        try:
            digest: str | None = settings_sha256(judge)
        except ValueError:
            digest = None
        low, high = core["score_range"]
        return cls(
            judge_class=core["class"],
            name=core["name"],
            model=core["model"],
            version=core["version"],
            score_range=(float(low), float(high)),
            higher_is_better=core["higher_is_better"],
            settings_sha256=digest,
        )

    @classmethod
    def unverified(cls, judge: Judge) -> JudgeFingerprint:
        """Only ``judge``'s name, model and version: what a run's rows can be checked against."""
        return cls(
            judge_class=None,
            name=judge.name,
            model=judge.model,
            version=judge.version,
            score_range=None,
            higher_is_better=None,
            settings_sha256=None,
        )

    def differences(self, other: JudgeFingerprint) -> list[str]:
        """What differs from ``other``, as "field: this != other"; empty when they match."""
        found = []
        for field in type(self).model_fields:
            mine, theirs = getattr(self, field), getattr(other, field)
            if mine is None or theirs is None or mine == theirs:
                continue
            if field == "settings_sha256":
                found.append("settings: its cache_settings() differ")
            else:
                found.append(f"{field}: {mine!r} != {theirs!r}")
        return found


class StateLimits(BaseModel):
    """The limits a run was started with. ``rate_limit`` is None for no limit."""

    model_config = {"extra": "ignore", "frozen": True}

    max_concurrency: int
    rate_limit: float | None


class AuditState(BaseModel):
    """``audit.json``: what resuming a run needs that its other files don't hold.

    Like ``results.json``, fields this version doesn't know are ignored, so a file from a newer
    version that only added fields still reads; ``STATE_SCHEMA_VERSION`` is bumped for any other
    change.
    """

    model_config = {"extra": "ignore", "frozen": True}

    schema_version: int = STATE_SCHEMA_VERSION
    dataset: str
    samples: int
    judge: JudgeFingerprint
    limits: StateLimits

    @classmethod
    def for_run(cls, judge: Judge, *, dataset: str, samples: int, limits: RunLimits) -> AuditState:
        return cls(
            dataset=dataset,
            samples=samples,
            judge=JudgeFingerprint.of(judge),
            limits=StateLimits(
                max_concurrency=limits.max_concurrency, rate_limit=limits.rate_limit
            ),
        )


def _fsync_dir(directory: Path) -> None:
    # So a rename survives a power cut. Windows can't open a directory, and doesn't need it.
    if sys.platform == "win32":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomically(path: Path, text: str) -> None:
    """Write ``text`` beside ``path``, make it durable, then move it into place."""
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(partial, path)
    _fsync_dir(path.parent)


def audit_state_text(state: AuditState) -> str:
    """``state`` as ``audit.json`` holds it.

    Raises:
        ValueError: if it can't be written as JSON, such as a judge with an infinite score
            range. ``check_can_start`` calls this, so that's found before anything is written.
    """
    data = state.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_audit_state(state: AuditState, run_dir: Path) -> Path:
    """Store ``state`` as ``audit.json`` in ``run_dir``, atomically."""
    path = run_dir / AUDIT_STATE_FILE
    _write_atomically(path, audit_state_text(state))
    return path


def read_audit_state(run_dir: Path) -> AuditState | None:
    """The run's ``audit.json``, or None if it has none.

    Raises:
        NotResumableError: if it can't be read, or is in a format this version doesn't read.
    """
    path = run_dir / AUDIT_STATE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NotResumableError(f"{path} can't be read: {exc}") from exc
    found = data.get("schema_version") if isinstance(data, dict) else None
    if found != STATE_SCHEMA_VERSION:
        raise NotResumableError(
            f"{path} has schema version {found!r}; this mtlj reads {STATE_SCHEMA_VERSION}"
        )
    try:
        return AuditState.model_validate(data)
    except ValidationError as exc:
        raise NotResumableError(f"{path} isn't valid: {exc}") from exc


@contextmanager
def lock_run(run_dir: Path) -> Iterator[None]:
    """Hold ``run_dir``'s lock for the block, so no other process scores into it meanwhile.

    Raises:
        RunInUseError: if another process holds it. It doesn't wait: a run can take hours.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    # thread_local=False: the lock belongs to the run, whichever thread releases it.
    lock = FileLock(run_dir / LOCK_FILE, timeout=0, thread_local=False)
    try:
        lock.acquire()
    except Timeout:
        raise RunInUseError(
            f"{run_dir} is in use by another mtlj process; wait for it to finish or stop it"
        ) from None
    try:
        yield
    finally:
        lock.release()


@dataclass(frozen=True)
class StoredRows:
    """What ``evaluations.jsonl`` holds.

    ``evaluations`` are its rows. ``torn_tail`` is a last line the process died while writing,
    which isn't a row. ``unterminated`` is True when the last row is whole but lacks its
    newline (the process died between the two): it's kept, since it was paid for, and the file
    needs its newline before anything is appended.
    """

    evaluations: list[Evaluation]
    torn_tail: bytes = b""
    unterminated: bool = False


def read_stored(path: Path) -> StoredRows:
    """Read every row of a results file; a missing file has none.

    Raises:
        NotResumableError: if a complete line isn't a valid evaluation; it names the line.
    """
    if not path.exists():
        return StoredRows([])
    data = path.read_bytes()
    body, newline, tail = data.rpartition(b"\n")
    if not newline:  # no newline at all: the whole file is its last line
        body, tail = b"", data
    evaluations = []
    for number, line in enumerate(body.split(b"\n"), start=1):
        if not line.strip():
            continue
        try:
            evaluations.append(Evaluation.model_validate_json(line))
        except ValidationError as exc:
            message = f"{path} line {number} isn't a valid evaluation: {exc}"
            raise NotResumableError(message) from exc
    if not tail.strip():
        return StoredRows(evaluations)
    try:
        # One write puts a whole row and its newline in the file, so a row cut short is never
        # valid JSON; one that parses lost only its newline.
        last = Evaluation.model_validate_json(tail)
    except ValidationError:
        return StoredRows(evaluations, torn_tail=tail)
    return StoredRows([*evaluations, last], unterminated=True)


def replace_rows(path: Path, evaluations: Sequence[Evaluation]) -> None:
    """Rewrite the results file to hold exactly ``evaluations``, atomically."""
    _write_atomically(path, "".join(e.model_dump_json() + "\n" for e in evaluations))


def append_superseded(run_dir: Path, evaluations: Sequence[Evaluation]) -> None:
    """Keep the failed rows a resume scores again, so the record of paid calls stays whole."""
    if not evaluations:
        return
    with (run_dir / SUPERSEDED_FILE).open("a", encoding="utf-8", newline="\n") as file:
        file.write("".join(e.model_dump_json() + "\n" for e in evaluations))
        file.flush()
        os.fsync(file.fileno())
