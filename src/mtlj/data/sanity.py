"""Dataset sanity checks: leave out records that would only produce meaningless mutants.

A degrading mutant is meant to make an answer contradict its source. That only means something
when the answer agreed with the source to begin with, and the source says enough to check it
against. ``check_dataset`` finds the records where that doesn't hold, so an audit can leave them
out before any mutant is made:

* ``short_context``: the passages hold fewer than ``MIN_CONTEXT_WORDS`` words in all, too little
  to support or contradict anything.
* ``unsupported_answer``: at least half of the answer's sentences are mostly absent from the
  context. A sentence counts as unsupported when under half of its content words (not stop
  words, at least two letters or a number) appear anywhere in the passages. Words are matched
  without case and with common endings removed, so "powered" matches "power". Sentences with
  fewer than ``MIN_SENTENCE_WORDS`` content words ("Here are some tips:") aren't counted.

The checks are plain word matching: fast, repeatable and free. They catch answers that drifted
away from their source, not answers that use the source's words to say something else.
"""

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spacy.lang.en.stop_words import STOP_WORDS

from mtlj.data.records import DatasetRecord

SHORT_CONTEXT = "short_context"
UNSUPPORTED_ANSWER = "unsupported_answer"
CHECKS = (SHORT_CONTEXT, UNSUPPORTED_ANSWER)
# Where an audit run writes the records it left out (see ``SanityReport.to_jsonl``).
EXCLUDED_FILE = "excluded.jsonl"

MIN_CONTEXT_WORDS = 50
MIN_SENTENCE_WORDS = 3
# A sentence is unsupported below this share of its content words found in the context...
MIN_SENTENCE_SUPPORT = 0.5
# ...and a record is excluded when at least this share of its sentences are unsupported.
MAX_UNSUPPORTED_SHARE = 0.5
# How many unsupported sentences a report quotes, to keep it readable.
QUOTED_SENTENCES = 3

WORD_RE = re.compile(r"\w+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
ENDINGS = ("ing", "ed", "es", "ly", "s")


@dataclass(frozen=True)
class Issue:
    """One reason a record is excluded: which check failed, why, and what it found."""

    check: str
    explanation: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExcludedRecord:
    """A record left out of an audit, with every reason."""

    record_id: str
    issues: tuple[Issue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"record_id": self.record_id, "issues": [asdict(i) for i in self.issues]}


@dataclass
class SanityReport:
    """The records that passed, and the ones excluded and why."""

    kept: list[DatasetRecord]
    excluded: list[ExcludedRecord]

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.excluded)

    @property
    def exclusion_rate(self) -> float:
        """The share of records excluded, 0 for no records."""
        return len(self.excluded) / self.total if self.total else 0.0

    def by_reason(self) -> dict[str, int]:
        """How many records each check excluded. A record can fail more than one."""
        return dict(Counter(i.check for r in self.excluded for i in r.issues))

    def summary(self) -> str:
        """One line for a person: how many were excluded, and why."""
        line = f"Excluded {len(self.excluded)} of {self.total} records ({self.exclusion_rate:.1%})"
        reasons = ", ".join(f"{check} {n}" for check, n in sorted(self.by_reason().items()))
        return f"{line}: {reasons}" if reasons else line

    def to_jsonl(self) -> str:
        """The excluded records, one JSON object per line, for a run's ``excluded.jsonl``."""
        return "".join(json.dumps(r.as_dict(), sort_keys=True) + "\n" for r in self.excluded)


def _stem(word: str) -> str:
    """The word without a common ending, so "powered" and "power" match."""
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _content_words(text: str) -> list[str]:
    words = (w.lower() for w in WORD_RE.findall(text))
    return [w for w in words if w not in STOP_WORDS and (len(w) >= 2 or w.isdigit())]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]


def _short_context(record: DatasetRecord) -> Issue | None:
    words = sum(len(WORD_RE.findall(p.text)) for p in record.context)
    if words >= MIN_CONTEXT_WORDS:
        return None
    return Issue(
        SHORT_CONTEXT,
        f"the context has {words} words, fewer than {MIN_CONTEXT_WORDS}",
        {"context_words": words},
    )


def _unsupported_answer(record: DatasetRecord) -> Issue | None:
    context = {_stem(w) for p in record.context for w in _content_words(p.text)}
    counted, unsupported = 0, []
    for sentence in _sentences(record.answer):
        words = _content_words(sentence)
        if len(words) < MIN_SENTENCE_WORDS:
            continue
        counted += 1
        found = sum(_stem(w) in context for w in words)
        if found / len(words) < MIN_SENTENCE_SUPPORT:
            unsupported.append(sentence)
    if not counted or len(unsupported) / counted < MAX_UNSUPPORTED_SHARE:
        return None
    return Issue(
        UNSUPPORTED_ANSWER,
        f"{len(unsupported)} of {counted} answer sentences are mostly absent from the context",
        {
            "unsupported_sentences": len(unsupported),
            "sentences": counted,
            "examples": unsupported[:QUOTED_SENTENCES],
        },
    )


def check_record(record: DatasetRecord) -> list[Issue]:
    """Every reason ``record`` would produce meaningless mutants, or [] when it's fine."""
    return [issue for issue in (_short_context(record), _unsupported_answer(record)) if issue]


def check_dataset(records: Iterable[DatasetRecord]) -> SanityReport:
    """Split ``records`` into those an audit can use and those it should leave out."""
    kept, excluded = [], []
    for record in records:
        issues = check_record(record)
        if issues:
            excluded.append(ExcludedRecord(record.record_id, tuple(issues)))
        else:
            kept.append(record)
    return SanityReport(kept, excluded)


def write_report(report: SanityReport, path: Path) -> None:
    """Write the excluded records to ``path`` as JSON Lines (see ``SanityReport.to_jsonl``)."""
    Path(path).write_text(report.to_jsonl(), encoding="utf-8", newline="\n")
