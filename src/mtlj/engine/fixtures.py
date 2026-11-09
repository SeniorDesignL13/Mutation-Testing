"""Record and replay judge scores from a fixture file, so tests and CI never call a judge (MTLJ-90).

A real judge costs money and needs a network and an API key. A ``FixtureStore`` captures its
scores once, in a JSONL file committed with the tests, and serves them back later with no judge
call at all.

**Record** (``FixtureStore(path, mode="record")``): pass the store to the runner as its
``cache``. A scoring already in the file is answered from it; any other calls the judge, and its
score is added. So recording again after a stop (a spending cap, a dropped connection) only pays
for the scorings still missing. ``refresh=True`` calls the judge for every scoring and replaces
what was there. Failures are never recorded.

**Replay** (``FixtureStore(path, mode="replay")``): the runner works out every scoring's key
before it starts, and raises ``FixtureMissingError`` listing the missing ones if the file doesn't
hold them all, without calling the judge for any. A replay run makes no judge call, ever.

**Keys** are the response cache's (``mtlj.engine.cache.CacheKeys``): the judge's class, name,
model, version, score range and ``cache_settings()``, the exact input, output and context, and
the sample index.

**Inputs** go with the scores. ``save_inputs`` writes the records and mutants that were scored
next to the fixture file, and replay loads them with ``load_inputs`` instead of generating
mutants again. Each line also names the judge that made it, and replay builds its judge with that
identity (``FixtureStore.judge``). So editing an operator, upgrading DeepEval or spaCy, or a
platform difference in what an operator produces doesn't break replay: the fixtures keep testing
the texts and judge they were recorded with. Re-recording is a deliberate step, taken when the
recorded behaviour should be brought up to date.

**The file** holds one JSON object per line, sorted by key, so recording again gives a small,
readable diff. While recording, each new score is appended at once to ``<path>.partial``, so a
run that dies keeps what it paid for; ``close()`` merges it into the file (written to a temporary
file, then renamed over it) and removes it. A store opened on a file whose ``.partial`` survived a
crash picks those scores up.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.cache import CachedScore, judge_identity
from mtlj.judges.base import Judge

logger = logging.getLogger(__name__)

Mode = Literal["record", "replay"]

# Bump when the line format changes. A file written in another format is refused.
FIXTURE_FORMAT_VERSION = 1


class FixtureMissingError(LookupError):
    """A replay run needs scorings the fixture file doesn't hold.

    ``missing`` is how many; the message names the first few and how to record them.
    """

    def __init__(self, message: str, missing: int) -> None:
        super().__init__(message)
        self.missing = missing

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.args[0], self.missing))


class FixtureFileError(ValueError):
    """The fixture file can't be read: a bad line, a wrong format, or two scores for one key."""


class FixtureJudge(BaseModel):
    """Everything about the judge that went into a fixture's key (``cache.judge_identity``).

    Replay reads it back to play the judge as it was recorded (``FixtureStore.replay_as``), so
    it holds the whole identity, not just what a reader needs to see.
    """

    model_config = {"extra": "forbid", "frozen": True, "populate_by_name": True}

    judge_class: str = Field(alias="class")
    name: str
    model: str
    version: str
    score_range: tuple[float, float]
    higher_is_better: bool
    settings: dict[str, Any]

    @classmethod
    def of(cls, judge: Judge) -> FixtureJudge:
        return cls.model_validate(judge_identity(judge))

    def differences(self, other: FixtureJudge, *, ignore: frozenset[str] = frozenset()) -> str:
        """The fields that differ, as ``field: ours != theirs``, or "" when none do."""
        ours, theirs = self.model_dump(by_alias=True), other.model_dump(by_alias=True)
        return "; ".join(
            f"{field}: {ours[field]!r} != {theirs[field]!r}"
            for field in ours
            if field not in ignore and ours[field] != theirs[field]
        )


class Fixture(BaseModel):
    """One line of a fixture file: a scoring's key, its judge, and its score."""

    model_config = {"extra": "forbid", "frozen": True}

    v: Literal[FIXTURE_FORMAT_VERSION] = FIXTURE_FORMAT_VERSION  # type: ignore[valid-type]
    key: str
    judge: FixtureJudge
    result: CachedScore


def _line_of(model: BaseModel) -> str:
    # Sorted keys and no spaces: one spelling per value, so diffs show only real changes.
    data = model.model_dump(mode="json", by_alias=True)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class FixtureStore:
    """Judge scores by cache key, read from and recorded to the JSONL file at ``path``.

    It has the response cache's ``get``/``put`` interface, so the runner takes it as its
    ``cache``; with ``mode="replay"`` its ``strict`` flag tells the runner never to call the
    judge.

    Args:
        path: the fixture file. In replay mode it must exist. In record mode it's created, with
            its folder, on ``close()`` if it doesn't.
        mode: ``"record"`` or ``"replay"`` (see the module docstring).
        refresh: record mode only: answer only from scores recorded since the refresh began
            (this store's, and a stopped refresh's journal), never from the file, and record
            every other score in place of what was there.

    Raises:
        FileNotFoundError: in replay mode, if the file doesn't exist.
        FixtureFileError: if the file (or a leftover ``.partial``) has a line that doesn't read,
            or two different scores under one key.
    """

    def __init__(self, path: Path, *, mode: Mode, refresh: bool = False) -> None:
        if mode not in ("record", "replay"):
            raise ValueError(f"mode must be 'record' or 'replay', got {mode!r}")
        if refresh and mode == "replay":
            raise ValueError("refresh only applies to record mode")
        self.path = Path(path)
        self.mode: Mode = mode
        self.refresh = refresh
        self.partial_path = self.path.with_name(self.path.name + ".partial")
        self._lock = threading.Lock()
        self._fixtures: dict[str, Fixture] = {}
        self._recorded = 0
        self._dropped = 0
        # Keys recorded since the last save: this store's, and a stopped run's journal.
        self._fresh: set[str] = set()
        self._journal: IO[str] | None = None

        if mode == "replay" and not self.path.exists():
            raise FileNotFoundError(
                f"no fixture file at {self.path}. Record one first (see docs/fixtures.md)."
            )
        if self.path.exists():
            self._load(self.path)
        if mode == "record" and self.partial_path.exists():
            # A record run that died left scores it paid for; keep them.
            found = self._load(self.partial_path, newer=True)
            logger.info("picked up %d recorded scores from %s", len(found), self.partial_path)
            self._recorded += len(found)
            self._fresh.update(found)

    @property
    def strict(self) -> bool:
        """True in replay mode: the runner must never call the judge."""
        return self.mode == "replay"

    def _load(self, path: Path, *, newer: bool = False) -> list[str]:
        """Read ``path`` into the store, returning the keys read. ``newer`` lines replace older
        scores for the same key."""
        loaded: list[str] = []
        seen: set[str] = set()
        complete = 0  # bytes up to the end of the last whole line
        torn = False
        with path.open(encoding="utf-8", newline="\n") as lines:
            for number, line in enumerate(lines, start=1):
                if newer and not line.endswith("\n"):
                    # The last line of a journal the process died while appending to.
                    logger.warning("dropped a partly written last line in %s", path)
                    torn = True
                    break
                complete += len(line.encode("utf-8"))
                if not line.strip():
                    continue
                try:
                    fixture = Fixture.model_validate_json(line)
                except ValidationError as exc:
                    raise FixtureFileError(f"{path}, line {number}: not a fixture: {exc}") from None
                old = self._fixtures.get(fixture.key)
                if not newer and fixture.key in seen and old != fixture:
                    raise FixtureFileError(
                        f"{path}, line {number}: a second, different score for key {fixture.key}"
                    )
                seen.add(fixture.key)
                self._fixtures[fixture.key] = fixture
                loaded.append(fixture.key)
        if torn:
            # Cut it off, or the next score appended would be glued onto it, and the journal
            # would hold a line that never reads again.
            with path.open("r+b") as file:
                file.truncate(complete)
        return loaded

    def get(self, key: str) -> CachedScore | None:
        """The score recorded under ``key``, or None.

        With ``refresh``, only a score recorded since the refresh began counts, so resuming a
        stopped refresh doesn't pay again for what it already recorded.
        """
        with self._lock:
            if self.refresh and key not in self._fresh:
                return None
            fixture = self._fixtures.get(key)
        return fixture.result if fixture is not None else None

    def __contains__(self, key: str) -> bool:
        """Whether ``get`` answers ``key``: with ``refresh``, only if recorded since."""
        return self.get(key) is not None

    def put(self, key: str, judge: Judge, score: CachedScore) -> None:
        """Record ``score`` under ``key``, appending it to the journal at once.

        Raises:
            RuntimeError: in replay mode, which never records.
        """
        if self.mode != "record":
            raise RuntimeError("a replay fixture store doesn't record scores")
        fixture = Fixture(key=key, judge=FixtureJudge.of(judge), result=score)
        with self._lock:
            if self._journal is None:
                self.partial_path.parent.mkdir(parents=True, exist_ok=True)
                self._journal = self.partial_path.open("a", encoding="utf-8", newline="\n")
            # One write per line, flushed, so a crash loses at most the line being written.
            self._journal.write(_line_of(fixture) + "\n")
            self._journal.flush()
            self._fixtures[key] = fixture
            self._fresh.add(key)
            self._recorded += 1

    @property
    def recorded(self) -> int:
        """How many scores this store has recorded that aren't in the file yet."""
        return self._recorded

    @property
    def judge(self) -> FixtureJudge | None:
        """The judge every fixture came from, or None when there are none.

        Replay builds its judge with this identity, so the keys match what was recorded even
        after the library behind the judge (DeepEval, say) has been upgraded.

        Raises:
            FixtureFileError: if the fixtures came from more than one judge or version, as a
                recording that stopped part way through a re-record leaves them.
        """
        with self._lock:
            # By their JSON: the settings are a dict, so FixtureJudge itself can't be hashed.
            judges = {_line_of(f.judge): f.judge for f in self._fixtures.values()}
        if len(judges) > 1:
            found = ", ".join(sorted(f"{j.name} {j.model} {j.version}" for j in judges.values()))
            raise FixtureFileError(
                f"{self.path} mixes scores from several judges or versions ({found}). "
                "Finish recording it (see docs/fixtures.md)."
            )
        return next(iter(judges.values()), None)

    def replay_as[J: Judge](self, judge: J) -> J:
        """Make ``judge`` the judge these fixtures were recorded with, and return it.

        ``judge`` must match the recorded identity in everything but its version: class, name,
        model, score range and ``cache_settings()``. Its version is set to the recorded one,
        since replay never runs the library behind it (DeepEval, say), so an upgrade mustn't
        change which scores replay looks up. Build ``judge`` so it can't score anything.

        Raises:
            FixtureFileError: if there are no fixtures, they mix judges (see ``judge``), or
                ``judge`` differs from the recorded one in more than its version.
        """
        recorded = self.judge
        if recorded is None:
            raise FixtureFileError(f"{self.path} holds no fixtures to replay")
        different = recorded.differences(FixtureJudge.of(judge), ignore=frozenset({"version"}))
        if different:
            raise FixtureFileError(
                f"{self.path} was recorded with another judge ({different}). Build the replay "
                "judge from the recorded one (see docs/fixtures.md)."
            )
        judge.version = recorded.version
        return judge

    def retain(self, keys: set[str]) -> int:
        """Drop every fixture whose key isn't in ``keys``; saved on ``close()``.

        The recorder calls this after a full run, so scores for texts or judge versions that are
        no longer recorded don't pile up in the file. Returns how many were dropped.

        Raises:
            RuntimeError: in replay mode, which never changes the file.
        """
        if self.mode != "record":
            raise RuntimeError("a replay fixture store doesn't change its file")
        with self._lock:
            stale = [key for key in self._fixtures if key not in keys]
            for key in stale:
                del self._fixtures[key]
            self._dropped += len(stale)
        return len(stale)

    def __len__(self) -> int:
        with self._lock:
            return len(self._fixtures)

    def save(self) -> None:
        """Write every fixture to ``path``, sorted by key, and remove the journal.

        The file is written next to ``path`` and renamed over it, so a crash part way leaves
        the old file whole. Does nothing in replay mode, or when nothing was recorded or dropped.
        """
        with self._lock:
            if self.mode != "record" or not (self._recorded or self._dropped):
                return
            if self._journal is not None:
                self._journal.close()
                self._journal = None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                for key in sorted(self._fixtures):
                    file.write(_line_of(self._fixtures[key]) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.partial_path.unlink(missing_ok=True)
            self._recorded = 0
            self._dropped = 0
            self._fresh.clear()

    def close(self, *, save: bool = True) -> None:
        """Save what was recorded (see ``save``), or with ``save=False`` only close the journal.

        Unsaved scores stay in ``<path>.partial``, and the next record store opened on ``path``
        picks them up. The recorder uses this for a run that didn't finish, so the committed
        file only ever changes from one complete recording to another.
        """
        if save:
            self.save()
            return
        with self._lock:
            if self._journal is not None:
                self._journal.close()
                self._journal = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # Saved even when the run raised (a spending cap, Ctrl-C): those scores were paid for.
        self.close()


RECORDS_FILE = "records.jsonl"
MUTANTS_FILE = "mutants.jsonl"


def save_inputs(directory: Path, records: list[DatasetRecord], mutants: list[Mutant]) -> None:
    """Write the records and mutants a fixture file was recorded on, for ``load_inputs``.

    One JSON object per line, in the order given, so a change shows as a readable diff. Each
    file is written beside its final name and renamed over it.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in ((RECORDS_FILE, records), (MUTANTS_FILE, mutants)):
        path = directory / name
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            for row in rows:
                file.write(row.model_dump_json() + "\n")
        os.replace(temporary, path)


def load_inputs(directory: Path) -> tuple[list[DatasetRecord], list[Mutant]]:
    """The records and mutants ``save_inputs`` wrote to ``directory``.

    Raises:
        FileNotFoundError: if either file is missing.
        FixtureFileError: if a line doesn't read.
    """
    directory = Path(directory)

    def read[T: BaseModel](name: str, model: type[T]) -> list[T]:
        path = directory / name
        rows = []
        with path.open(encoding="utf-8") as lines:
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(model.model_validate_json(line))
                except ValidationError as exc:
                    raise FixtureFileError(f"{path}, line {number}: {exc}") from None
        return rows

    return read(RECORDS_FILE, DatasetRecord), read(MUTANTS_FILE, Mutant)
