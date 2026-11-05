"""Content-addressed response cache, so a re-run doesn't pay again for the same calls (MTLJ-93).

Developing an operator or a metric means running the same audit many times. Most of its calls
are identical from one run to the next, and a hosted judge charges for every one. With a
``ResponseCache``, the runner answers a scoring the cache holds from it, without calling the
judge, and stores every new score it gets.

**The key** is the SHA-256 of one canonical JSON document holding:

* the judge: its class, ``name``, ``model``, ``version``, ``score_range``,
  ``higher_is_better`` and ``cache_settings()`` (``mtlj.judges.base``, "Caching"), so two
  configs of a judge never share a score;
* the call: the exact ``input``, ``output`` and ``context`` the judge would be given;
* the sample index.

The sample index is in the key because repeated sampling needs fresh calls: each of a run's N
samples of a text is its own call, and so its own entry, and the spread between them is the
judge's noise (docs/metrics.md). A re-run gets back the same N samples; a run with more samples
calls the judge for the new ones only. For fresh samples of texts already cached, run without a
cache, or with one opened ``refresh=True``: it calls the judge every time and stores what it
gets in place of what was there.

**What's stored** is the score and what came with it (raw score, verdict counts, claim count)
and the original call's latency. Failures are never stored, so a call that failed is tried again
next run. ``CACHE_KEY_VERSION`` goes into every key: changing what the key covers means bumping
it, which retires every entry made the old way.

**The file** is one SQLite database. Lookups and writes happen on the event loop's thread and
take well under a millisecond, so they don't hold up the judge calls. Several runs can share a
file, one at a time or at once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from pydantic import BaseModel, Field, ValidationError

from mtlj.judges.base import Context, Judge

logger = logging.getLogger(__name__)

# Bump when the key covers something new or encodes something differently.
CACHE_KEY_VERSION = 1
# Bump when the database's tables change. A file made by another version is refused.
SCHEMA_VERSION = 1


def _canonical(value: Any) -> str:
    """One JSON spelling for a value: sorted keys, no spaces, no NaN."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def judge_identity(judge: Judge) -> dict[str, Any]:
    """Everything about ``judge`` that goes into a cache key.

    Raises:
        ValueError: if its ``cache_settings()`` can't be written as JSON. Caching such a judge
            could give one config's scores to another.
    """
    cls = type(judge)
    settings = judge.cache_settings()
    if not isinstance(settings, Mapping):
        raise ValueError(
            f"judge {judge.name!r} can't be cached: cache_settings() returned "
            f"{type(settings).__name__}, not a mapping"
        )
    identity = {
        "class": f"{cls.__module__}.{cls.__qualname__}",
        "name": judge.name,
        "model": judge.model,
        "version": judge.version,
        "score_range": list(judge.score_range),
        "higher_is_better": bool(judge.higher_is_better),
        "settings": dict(settings),
    }
    for key, value in identity["settings"].items():
        try:
            _canonical(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"judge {judge.name!r} can't be cached: its setting {key!r} = {value!r} can't be "
                "written as JSON. Override cache_settings() to say what decides its scores "
                '(see mtlj.judges.base, "Caching"), or run without a cache.'
            ) from None
    try:
        _canonical(identity)
    except (TypeError, ValueError):
        raise ValueError(
            f"judge {judge.name!r} can't be cached: its cache settings can't be written as JSON"
        ) from None
    return identity


class CacheKeys:
    """Makes the cache key for each of one judge's calls.

    The judge's part is worked out once, when this is made, so a judge that can't be cached is
    refused before any call.
    """

    def __init__(self, judge: Judge) -> None:
        self._judge = _canonical(judge_identity(judge))

    def __call__(self, input: str, output: str, context: Context, sample_index: int) -> str:
        call = _canonical(
            {"input": input, "output": output, "context": context, "sample_index": sample_index}
        )
        document = f'{{"v":{CACHE_KEY_VERSION},"judge":{self._judge},"call":{call}}}'
        return hashlib.sha256(document.encode("utf-8")).hexdigest()


class CachedScore(BaseModel):
    """What the cache keeps for one call: the score, what came with it, and its latency."""

    model_config = {"extra": "forbid", "frozen": True}

    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    raw_score: float = Field(allow_inf_nan=False)
    verdict_yes: int | None = Field(default=None, ge=0)
    verdict_borderline: int | None = Field(default=None, ge=0)
    verdict_no: int | None = Field(default=None, ge=0)
    n_claims: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0.0)


class ResponseCache:
    """Judge scores by cache key, kept in one SQLite file at ``path``.

    Args:
        path: the database file. It and its folder are created if they don't exist.
        refresh: never answer from the cache, but still store every score. A run with it calls
            the judge for every scoring and leaves its new scores in the cache.

    Raises:
        ValueError: if the file was made by a version of mtlj with a different schema.
    """

    def __init__(self, path: Path, *, refresh: bool = False) -> None:
        self.path = Path(path)
        self.refresh = refresh
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection, used from whichever thread holds the lock. Autocommit: each write is
        # its own transaction, so a run that dies keeps everything stored before it.
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        try:
            self._open()
        except BaseException:
            self._db.close()
            raise

    def _open(self) -> None:
        # WAL lets several runs read while one writes; NORMAL skips a sync per write, which in
        # WAL mode can lose the last writes on a power cut but never corrupts the file.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        (version,) = self._db.execute("PRAGMA user_version").fetchone()
        if version == 0:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                " key TEXT PRIMARY KEY,"
                " judge_name TEXT NOT NULL,"
                " model TEXT NOT NULL,"
                " version TEXT NOT NULL,"
                " result TEXT NOT NULL,"
                " stored_at TEXT NOT NULL"
                ") WITHOUT ROWID"
            )
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise ValueError(
                f"{self.path} is a response cache with schema version {version}; this version "
                f"of mtlj reads version {SCHEMA_VERSION}. Use another file."
            )

    def get(self, key: str) -> CachedScore | None:
        """The score stored under ``key``, or None. Always None with ``refresh``.

        An entry that no longer reads (edited by hand, or written by a bug) counts as missing,
        with a warning, and the next score stored under its key replaces it.
        """
        if self.refresh:
            return None
        with self._lock:
            row = self._db.execute("SELECT result FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return CachedScore.model_validate_json(row[0])
        except ValidationError as exc:
            logger.warning("ignored an unreadable response cache entry %s: %s", key, exc)
            return None

    def put(self, key: str, judge: Judge, score: CachedScore) -> None:
        """Store ``score`` under ``key``, in place of anything stored there before."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses"
                " (key, judge_name, model, version, result, stored_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    judge.name,
                    judge.model,
                    judge.version,
                    score.model_dump_json(),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def __len__(self) -> int:
        with self._lock:
            (count,) = self._db.execute("SELECT COUNT(*) FROM responses").fetchone()
        return count

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
