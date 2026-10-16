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
