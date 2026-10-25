"""Loader for the frozen RAGTruth corpus (see docs/corpus-v1.md).

Converts each line of ``corpus/v1/corpus_v1.jsonl`` into a ``DatasetRecord``.
This is the only place that knows the RAGTruth line format; everything
downstream reads ``DatasetRecord``.

Text is passed through untouched. The freeze already stripped and split it,
and operators depend on the exact strings.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mtlj.data.records import DatasetRecord

logger = logging.getLogger(__name__)

SOURCE_DATASET = "ragtruth"

# The repo is mounted at /app in Docker, so this resolves there too.
CORPUS_V1_DIR = Path(__file__).resolve().parents[3] / "corpus" / "v1"
CORPUS_V1_PATH = CORPUS_V1_DIR / "corpus_v1.jsonl"
CORPUS_V1_MANIFEST = CORPUS_V1_DIR / "manifest.json"


# Every corpus line has exactly these fields. Anything else means the format changed.
FIELDS = frozenset({"id", "question", "context", "answer", "split", "model", "gold_set"})


class ChecksumMismatchError(Exception):
    """The corpus file is not the one the manifest describes."""


class IncompleteCorpusError(Exception):
    """The frozen corpus loaded fewer records than its manifest promises."""


class _MalformedLineError(ValueError):
    """A corpus line is not in the RAGTruth format. The line is skipped, not fatal."""


@dataclass
class SkippedRecord:
    """A corpus line that could not be loaded, and why."""

    line_number: int
    source_id: str | None
    reason: str


@dataclass
class LoadResult:
    """Records that loaded, plus every line that was skipped."""

    records: list[DatasetRecord] = field(default_factory=list)
    skipped: list[SkippedRecord] = field(default_factory=list)


def _summarize(error: ValidationError) -> str:
    """One line per error, without echoing the record's text back into the logs."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors(include_url=False)
    )


def _field(raw: dict, key: str, kind: type) -> Any:
    """Return ``raw[key]``, raising KeyError if missing or _MalformedLineError if the wrong type."""
    value = raw[key]
    if not isinstance(value, kind):
        raise _MalformedLineError(
            f"field {key!r} must be {kind.__name__}, got {type(value).__name__}"
        )
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json.loads hook: a repeated key would otherwise silently keep the last value."""
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise _MalformedLineError(f"duplicate key {key!r}")
        seen.add(key)
    return dict(pairs)


def _to_record(raw: dict) -> DatasetRecord:
    """Map one corpus line to a DatasetRecord.

    Raises KeyError for a missing field, _MalformedLineError for an unexpected
    field, a field of the wrong type or a blank id, and ValidationError for
    anything DatasetRecord rejects.
    """
    unexpected = raw.keys() - FIELDS
    if unexpected:
        raise _MalformedLineError(f"unexpected field(s) {sorted(unexpected)}")
    source_id = _field(raw, "id", str)
    if not source_id.strip():
        raise _MalformedLineError("field 'id' must not be blank")
    record_id = f"{SOURCE_DATASET}_{source_id}"
    context = _field(raw, "context", list)
    # Passages are passed as dicts so validation errors name their position,
    # e.g. "context.1.text".
    return DatasetRecord(
        record_id=record_id,
        source_dataset=SOURCE_DATASET,
        question=_field(raw, "question", str),
        context=[
            {"passage_id": f"{record_id}_p{i}", "text": text} for i, text in enumerate(context)
        ],
        answer=_field(raw, "answer", str),
        metadata={
            "source_id": source_id,
            "split": _field(raw, "split", str),
            "model": _field(raw, "model", str),
            "gold_set": _field(raw, "gold_set", bool),
        },
    )


def load_ragtruth(path: Path, expected_sha256: str | None = None) -> LoadResult:
    """Load a RAGTruth corpus file, skipping and logging malformed lines.

    If ``expected_sha256`` is given, a mismatch raises ``ChecksumMismatchError``:
    a changed corpus is fatal, a bad line is not. The hash covers the same bytes
    that are parsed, in one pass, so the file can't change between check and load.
    """
    result = LoadResult()
    digest = hashlib.sha256()
    seen: set[str] = set()

    # Read bytes and decode per line, so one bad byte skips a line, not the file.
    with path.open("rb") as f:
        for line_number, line_bytes in enumerate(f, start=1):
            digest.update(line_bytes)
            if not line_bytes.strip():
                continue

            source_id = None
            try:
                # utf-8-sig also drops a byte-order mark at the start of the file.
                raw = json.loads(
                    line_bytes.decode("utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
                )
                if not isinstance(raw, dict):
                    raise _MalformedLineError("line is not a JSON object")
                if isinstance(raw.get("id"), str):
                    source_id = raw["id"]
                record = _to_record(raw)
            except UnicodeDecodeError:
                reason = "invalid UTF-8"
            except json.JSONDecodeError as e:
                reason = f"invalid JSON: {e.msg}"
            except RecursionError:
                reason = "invalid JSON: nested too deeply"
            except KeyError as e:
                reason = f"missing field {e.args[0]!r}"
            except ValidationError as e:
                reason = f"invalid record: {_summarize(e)}"
            # Only the loader's own format errors. Any other exception is a bug and must not be
            # hidden as a skipped line.
            except _MalformedLineError as e:
                reason = f"invalid record: {e}"
            else:
                if record.record_id in seen:
                    reason = "duplicate id"
                else:
                    seen.add(record.record_id)
                    result.records.append(record)
                    continue

            # %r, so an id containing newlines can't forge extra log lines.
            logger.warning(
                "Skipping %s line %d (id=%r): %s", path.name, line_number, source_id, reason
            )
            result.skipped.append(SkippedRecord(line_number, source_id, reason))

    if expected_sha256 is not None:
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            raise ChecksumMismatchError(f"{path} has sha256 {actual}, expected {expected_sha256}")

    logger.info(
        "Loaded %d records from %s, skipped %d",
        len(result.records),
        path.name,
        len(result.skipped),
    )
    return result


def load_corpus_v1() -> LoadResult:
    """Load the frozen v1 corpus, verified against its manifest.

    The frozen corpus has no bad lines, so unlike ``load_ragtruth`` a skipped
    line or a short count raises ``IncompleteCorpusError`` instead of quietly
    returning fewer records.
    """
    manifest = json.loads(CORPUS_V1_MANIFEST.read_text(encoding="utf-8"))
    result = load_ragtruth(CORPUS_V1_PATH, expected_sha256=manifest["sha256"])
    if result.skipped or len(result.records) != manifest["record_count"]:
        raise IncompleteCorpusError(
            f"loaded {len(result.records)} of {manifest['record_count']} records, "
            f"skipped {len(result.skipped)}: "
            + "; ".join(f"line {s.line_number}: {s.reason}" for s in result.skipped[:5])
        )
    return result
