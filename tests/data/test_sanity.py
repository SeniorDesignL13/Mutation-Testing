"""Tests for the dataset sanity checks."""

import json
from pathlib import Path

import pytest

from mtlj.data.records import DatasetRecord
from mtlj.data.sanity import (
    EXCLUDED_FILE,
    MIN_CONTEXT_WORDS,
    SHORT_CONTEXT,
    UNSUPPORTED_ANSWER,
    check_dataset,
    check_record,
    write_report,
)

CONTEXT = (
    "The Golden Gate Bridge opened to traffic in 1937. It spans 1,280 metres across the strait "
    "and was painted international orange so ships could see it in fog. Joseph Strauss led the "
    "engineering team, and construction began in 1933. Today the bridge carries about 112,000 "
    "vehicles every day between San Francisco and Marin County."
)
SUPPORTED = (
    "The Golden Gate Bridge opened in 1937. It is painted international orange so ships can see "
    "it in fog. About 112,000 vehicles cross it every day."
)
UNSUPPORTED = (
    "Penguins huddle together to survive Antarctic winters. Emperor penguins dive deeper than "
    "any other bird. The Golden Gate Bridge opened in 1937."
)


def _record(answer: str, *passages: str, record_id: str = "r1") -> DatasetRecord:
    return DatasetRecord(
        record_id=record_id,
        source_dataset="test",
        question="Tell me about the bridge.",
        context=[{"passage_id": f"p{i}", "text": t} for i, t in enumerate(passages or [CONTEXT])],
        answer=answer,
    )


def _checks(record: DatasetRecord) -> set[str]:
    return {issue.check for issue in check_record(record)}


# -- short context ---------------------------------------------------------------------------


def test_a_well_supported_record_passes() -> None:
    assert check_record(_record(SUPPORTED)) == []


@pytest.mark.parametrize("context", ["Bridge.", "The bridge opened in 1937.", " - "])
def test_trivially_short_context_is_flagged(context: str) -> None:
    [issue] = [i for i in check_record(_record(SUPPORTED, context)) if i.check == SHORT_CONTEXT]
    assert issue.detail["context_words"] < MIN_CONTEXT_WORDS
    assert str(MIN_CONTEXT_WORDS) in issue.explanation


def test_context_is_counted_across_all_passages() -> None:
    half = CONTEXT.split(". ")
    record = _record(SUPPORTED, ". ".join(half[:2]), ". ".join(half[2:]))
    assert SHORT_CONTEXT not in _checks(record)


# -- unsupported answers ---------------------------------------------------------------------


def test_an_answer_mostly_absent_from_its_context_is_flagged() -> None:
    [issue] = check_record(_record(UNSUPPORTED))
    assert issue.check == UNSUPPORTED_ANSWER
    assert issue.detail["unsupported_sentences"] == 2
    assert issue.detail["sentences"] == 3
    assert issue.explanation == "2 of 3 answer sentences are mostly absent from the context"
    assert issue.detail["examples"][0].startswith("Penguins huddle")


def test_a_refusal_is_flagged() -> None:
    # Common in RAGTruth: nothing true to make false.
    record = _record("Unable to answer based on given passages.")
    assert _checks(record) == {UNSUPPORTED_ANSWER}


def test_one_unsupported_sentence_among_several_supported_ones_passes() -> None:
    answer = SUPPORTED + " Penguins huddle together to survive Antarctic winters."
    assert check_record(_record(answer)) == []


def test_word_endings_and_case_dont_matter() -> None:
    answer = "Engineers began constructing the bridge in 1933, painting it ORANGE for the fog."
    assert check_record(_record(answer)) == []


def test_sentences_too_short_to_judge_are_not_counted() -> None:
    answer = "Here are some facts: " + SUPPORTED + " Hope this helps!"
    assert check_record(_record(answer)) == []


def test_a_record_can_fail_both_checks() -> None:
    record = _record(UNSUPPORTED, "The bridge opened in 1937.")
    assert _checks(record) == {SHORT_CONTEXT, UNSUPPORTED_ANSWER}


# -- the report ------------------------------------------------------------------------------


def _dataset() -> list[DatasetRecord]:
    return [
        _record(SUPPORTED, record_id="good"),
        _record(UNSUPPORTED, record_id="drifted"),
        _record(SUPPORTED, "Too short.", record_id="short"),
        _record(SUPPORTED, record_id="good2"),
    ]


def test_check_dataset_keeps_good_records_in_order() -> None:
    report = check_dataset(_dataset())
    assert [r.record_id for r in report.kept] == ["good", "good2"]
    assert [r.record_id for r in report.excluded] == ["drifted", "short"]


def test_report_gives_the_exclusion_rate_and_reasons() -> None:
    report = check_dataset(_dataset())
    assert report.total == 4
    assert report.exclusion_rate == 0.5
    # "short" fails both checks: two words of context can't support the answer either.
    assert report.by_reason() == {UNSUPPORTED_ANSWER: 2, SHORT_CONTEXT: 1}
    assert report.summary() == (
        "Excluded 2 of 4 records (50.0%): short_context 1, unsupported_answer 2"
    )


def test_an_empty_dataset_excludes_nothing() -> None:
    report = check_dataset([])
    assert (report.total, report.exclusion_rate) == (0, 0.0)
    assert report.summary() == "Excluded 0 of 0 records (0.0%)"


def test_report_writes_one_line_per_excluded_record(tmp_path: Path) -> None:
    path = tmp_path / EXCLUDED_FILE
    write_report(check_dataset(_dataset()), path)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["record_id"] for line in lines] == ["drifted", "short"]
    issue = lines[0]["issues"][0]
    assert issue["check"] == UNSUPPORTED_ANSWER
    assert issue["explanation"]
    assert issue["detail"]["examples"]
    assert b"\r\n" not in path.read_bytes()


def test_nothing_excluded_writes_an_empty_report(tmp_path: Path) -> None:
    path = tmp_path / EXCLUDED_FILE
    write_report(check_dataset([_record(SUPPORTED)]), path)
    assert path.read_text(encoding="utf-8") == ""
