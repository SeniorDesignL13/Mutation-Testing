"""Tests for the canonical dataset record format."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mtlj.data.records import DatasetRecord, Passage

FIXTURE = Path(__file__).parent / "example_records.jsonl"


def _record(**overrides) -> dict:
    base = {
        "record_id": "t_1",
        "source_dataset": "test",
        "question": "Q?",
        "context": [{"passage_id": "p1", "text": "Some text."}],
        "answer": "A.",
    }
    return {**base, **overrides}


def test_valid_record_parses() -> None:
    rec = DatasetRecord(**_record())
    assert rec.context[0].title is None
    assert rec.metadata == {}


def test_empty_context_rejected() -> None:
    with pytest.raises(ValidationError):
        DatasetRecord(**_record(context=[]))


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        DatasetRecord(**_record(unexpected="x"))


def test_fixture_loads_and_ids_unique() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    records = [DatasetRecord(**json.loads(line)) for line in lines if line.strip()]
    assert len(records) == 3
    assert len({r.record_id for r in records}) == len(records)
    assert any(len(r.context) > 1 for r in records)
    assert isinstance(records[0].context[0], Passage)


@pytest.mark.parametrize("field", ["question", "answer"])
@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_question_or_answer_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        DatasetRecord(**_record(**{field: value}))


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_passage_text_rejected(text: str) -> None:
    with pytest.raises(ValidationError):
        DatasetRecord(**_record(context=[{"passage_id": "p1", "text": text}]))


def test_surrounding_whitespace_preserved() -> None:
    rec = DatasetRecord(**_record(answer="  A.\n"))
    assert rec.answer == "  A.\n"


def test_fixture_round_trips_through_json() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    for line in filter(str.strip, lines):
        rec = DatasetRecord.model_validate_json(line)
        assert DatasetRecord.model_validate_json(rec.model_dump_json()) == rec
        assert json.loads(rec.model_dump_json()) == json.loads(line)
