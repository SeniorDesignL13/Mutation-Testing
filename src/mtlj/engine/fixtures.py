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
the sample index. A change to any of them (a new DeepEval version, an edited operator) means
different keys, and replay fails until the fixtures are recorded again. That's on purpose: a
fixture recorded for other inputs or another judge would test something that no longer exists.

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
from typing import IO, Literal, Self

from pydantic import BaseModel, ValidationError

from mtlj.engine.cache import CachedScore
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
    """Which judge a fixture came from. For people reading the file; the key already covers it."""

    model_config = {"extra": "forbid", "frozen": True}

    name: str
    model: str
    version: str


class Fixture(BaseModel):
    """One line of a fixture file: a scoring's key, its judge, and its score."""

    model_config = {"extra": "forbid", "frozen": True}

    v: Literal[1] = FIXTURE_FORMAT_VERSION
    key: str
    judge: FixtureJudge
    result: CachedScore


def _line(fixture: Fixture) -> str:
    # Sorted keys and no spaces: one spelling per fixture, so diffs show only real changes.
    return json.dumps(fixture.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class FixtureStore:
    """Judge scores by cache key, read from and recorded to the JSONL file at ``path``.

    It has the response cache's ``get``/``put`` interface, so the runner takes it as its
    ``cache``; with ``mode="replay"`` its ``strict`` flag tells the runner never to call the
    judge.

    Args:
        path: the fixture file. In replay mode it must exist. In record mode it's created, with
            its folder, on ``close()`` if it doesn't.
        mode: ``"record"`` or ``"replay"`` (see the module docstring).
        refresh: record mode only: never answer from the file, but record every score, in place
            of what was there.

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
            logger.info("picked up %d recorded scores from %s", found, self.partial_path)
            self._recorded += found

    @property
    def strict(self) -> bool:
        """True in replay mode: the runner must never call the judge."""
        return self.mode == "replay"

    def _load(self, path: Path, *, newer: bool = False) -> int:
        """Read ``path`` into the store. ``newer`` lines replace older scores for the same key."""
        count = 0
        seen: set[str] = set()
        with path.open(encoding="utf-8", newline="\n") as lines:
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                if newer and not line.endswith("\n"):
                    # The last line of a journal the process died while appending to.
                    logger.warning("skipped a partly written last line in %s", path)
                    break
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
                count += 1
        return count

    def get(self, key: str) -> CachedScore | None:
        """The score recorded under ``key``, or None. Always None with ``refresh``."""
        if self.refresh:
            return None
        with self._lock:
            fixture = self._fixtures.get(key)
        return fixture.result if fixture is not None else None

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._fixtures

    def put(self, key: str, judge: Judge, score: CachedScore) -> None:
        """Record ``score`` under ``key``, appending it to the journal at once.

        Raises:
            RuntimeError: in replay mode, which never records.
        """
        if self.mode != "record":
            raise RuntimeError("a replay fixture store doesn't record scores")
        fixture = Fixture(
            key=key,
            judge=FixtureJudge(name=judge.name, model=judge.model, version=judge.version),
            result=score,
        )
        with self._lock:
            if self._journal is None:
                self.partial_path.parent.mkdir(parents=True, exist_ok=True)
                self._journal = self.partial_path.open("a", encoding="utf-8", newline="\n")
            # One write per line, flushed, so a crash loses at most the line being written.
            self._journal.write(_line(fixture) + "\n")
            self._journal.flush()
            self._fixtures[key] = fixture
            self._recorded += 1

    @property
    def recorded(self) -> int:
        """How many scores this store has recorded that aren't in the file yet."""
        return self._recorded

    def __len__(self) -> int:
        with self._lock:
            return len(self._fixtures)

    def save(self) -> None:
        """Write every fixture to ``path``, sorted by key, and remove the journal.

        The file is written next to ``path`` and renamed over it, so a crash part way leaves
        the old file whole. Does nothing in replay mode, or when nothing was recorded.
        """
        with self._lock:
            if self.mode != "record" or not self._recorded:
                return
            if self._journal is not None:
                self._journal.close()
                self._journal = None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                for key in sorted(self._fixtures):
                    file.write(_line(self._fixtures[key]) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            self.partial_path.unlink(missing_ok=True)
            self._recorded = 0

    def close(self) -> None:
        """Save what was recorded (see ``save``)."""
        self.save()

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
