"""Tests for the RAGTruth corpus loader."""

import hashlib
import json
import logging
from pathlib import Path

import pytest

import mtlj.data.ragtruth as ragtruth
from mtlj.data.ragtruth import (
    CORPUS_V1_DIR,
    CORPUS_V1_MANIFEST,
    ChecksumMismatchError,
    IncompleteCorpusError,
    LoadResult,
    SkippedRecord,
    load_corpus_v1,
    load_ragtruth,
)

SAMPLE = Path(__file__).parent / "ragtruth_sample.jsonl"


def _raw_lines() -> list[dict]:
    # split("\n") like the loader: splitlines() would also split on U+2028 and friends,
    # which are legal inside JSON strings.
    text = SAMPLE.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def test_sample_maps_fields() -> None:
    result = load_ragtruth(SAMPLE)
    assert result.skipped == []
    assert [r.record_id for r in result.records] == [
        "ragtruth_11856",
        "ragtruth_11862",
        "ragtruth_11868",
        "ragtruth_11904",
    ]

    rec = result.records[2]
    assert rec.source_dataset == "ragtruth"
    assert [p.passage_id for p in rec.context] == [
        "ragtruth_11868_p0",
        "ragtruth_11868_p1",
        "ragtruth_11868_p2",
    ]
    assert all(p.title is None for p in rec.context)
    assert rec.metadata == {
        "source_id": "11868",
        "split": "train",
        "model": "gpt-4-0613",
        "gold_set": True,
    }


def test_conversion_is_lossless() -> None:
    for rec, raw in zip(load_ragtruth(SAMPLE).records, _raw_lines(), strict=True):
        assert rec.question == raw["question"]
        assert rec.answer == raw["answer"]
        assert [p.text for p in rec.context] == raw["context"]
        assert rec.metadata == {
            "source_id": raw["id"],
            "split": raw["split"],
            "model": raw["model"],
            "gold_set": raw["gold_set"],
        }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"id": None}, "field 'id' must be str"),
        ({"id": ""}, "field 'id' must not be blank"),
        ({"id": 11856}, "field 'id' must be str"),
        ({"context": "text"}, "field 'context' must be list"),
        ({"context": [1, 2]}, "context.0.text: Input should be a valid string"),
        ({"answer": 5}, "field 'answer' must be str"),
        ({"question": None}, "field 'question' must be str"),
        ({"answer_v2": "new"}, "unexpected field(s) ['answer_v2']"),
        ({"question": "  "}, "question: Value error, must not be empty or whitespace"),
        ({"gold_set": "yes"}, "field 'gold_set' must be bool"),
        ({"split": None}, "field 'split' must be str"),
    ],
)
def test_wrongly_typed_fields_skipped(tmp_path: Path, overrides: dict, reason: str) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text(json.dumps({**_raw_lines()[0], **overrides}) + "\n", encoding="utf-8")
    result = load_ragtruth(path)
    assert result.records == []
    assert reason in result.skipped[0].reason


def test_invalid_utf8_skips_line_not_file(tmp_path: Path) -> None:
    good = SAMPLE.read_bytes().splitlines()
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(b"\n".join([good[0], b'{"id": "\xff\xfe"}', good[1]]) + b"\n")
    result = load_ragtruth(path)
    assert [r.record_id for r in result.records] == ["ragtruth_11856", "ragtruth_11862"]
    assert [(s.line_number, s.reason) for s in result.skipped] == [(2, "invalid UTF-8")]


def test_deeply_nested_line_skipped_not_fatal(tmp_path: Path) -> None:
    good = SAMPLE.read_bytes().splitlines()
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(b"\n".join([good[0], b"[" * 100_000, good[1]]) + b"\n")
    result = load_ragtruth(path)
    assert len(result.records) == 2
    assert [s.reason for s in result.skipped] == ["invalid JSON: nested too deeply"]


def test_byte_order_mark_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" + SAMPLE.read_bytes())
    result = load_ragtruth(path)
    assert result.skipped == []
    assert result.records == load_ragtruth(SAMPLE).records


def test_skip_reason_is_one_line_without_record_text(tmp_path: Path) -> None:
    raw = {**_raw_lines()[0], "context": ["fine", 1]}
    path = tmp_path / "corpus.jsonl"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    [skipped] = load_ragtruth(path).skipped
    assert skipped.reason == "invalid record: context.1.text: Input should be a valid string"


def test_malformed_lines_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good = SAMPLE.read_text(encoding="utf-8").splitlines()
    missing_answer = {k: v for k, v in _raw_lines()[0].items() if k != "answer"}
    blank_answer = {**_raw_lines()[1], "id": "90001", "answer": "  "}
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        "\n".join(
            [
                good[0],
                "{not json",
                json.dumps({**missing_answer, "id": "90000"}),
                json.dumps(blank_answer),
                good[0],  # duplicate id
                good[1],
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="mtlj.data.ragtruth"):
        result = load_ragtruth(path)

    assert [r.record_id for r in result.records] == ["ragtruth_11856", "ragtruth_11862"]
    skipped = {s.line_number: s for s in result.skipped}
    assert set(skipped) == {2, 3, 4, 5}
    assert skipped[2].reason.startswith("invalid JSON")
    assert skipped[3].reason == "missing field 'answer'"
    assert skipped[3].source_id == "90000"
    assert skipped[4].reason.startswith("invalid record")
    assert skipped[5].reason == "duplicate id"
    assert len(caplog.records) == 4


def test_duplicate_key_skipped_not_last_one_wins(tmp_path: Path) -> None:
    line = json.dumps(_raw_lines()[0])
    path = tmp_path / "corpus.jsonl"
    path.write_text(line[:-1] + ', "answer": "replaced"}\n', encoding="utf-8")

    result = load_ragtruth(path)

    assert result.records == []
    assert [s.reason for s in result.skipped] == ["invalid record: duplicate key 'answer'"]


def test_skip_log_escapes_newlines_in_id(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    raw = {**_raw_lines()[0], "id": "1\nERROR forged entry", "answer": " "}
    path = tmp_path / "corpus.jsonl"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="mtlj.data.ragtruth"):
        load_ragtruth(path)

    [message] = [r.getMessage() for r in caplog.records]
    assert "\n" not in message
    assert "'1\\nERROR forged entry'" in message


def test_unexpected_errors_are_not_hidden_as_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug in the conversion must crash the load, not skip every line as 'invalid'."""

    def broken(raw: dict) -> None:
        raise TypeError("bug in the loader")

    monkeypatch.setattr(ragtruth, "_to_record", broken)
    with pytest.raises(TypeError, match="bug in the loader"):
        load_ragtruth(SAMPLE)


def test_load_is_repeatable() -> None:
    assert load_ragtruth(SAMPLE) == load_ragtruth(SAMPLE)


def test_checksum_mismatch_raises() -> None:
    with pytest.raises(ChecksumMismatchError):
        load_ragtruth(SAMPLE, expected_sha256="0" * 64)


def test_checksum_accepts_uppercase_digest() -> None:
    digest = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    assert load_ragtruth(SAMPLE, expected_sha256=digest.upper()).records


def test_checksum_mismatch_raises_even_with_good_lines(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(SAMPLE.read_bytes())
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(SAMPLE.read_bytes() + b"\n")  # One extra blank line changes the bytes

    with pytest.raises(ChecksumMismatchError):
        load_ragtruth(path, expected_sha256=expected)


def test_checksum_files_agree() -> None:
    """corpus_v1.sha256 (for sha256sum -c) and the manifest must name the same digest."""
    manifest = json.loads(CORPUS_V1_MANIFEST.read_text(encoding="utf-8"))
    digest, name = (CORPUS_V1_DIR / "corpus_v1.sha256").read_text(encoding="utf-8").split()

    assert name == "corpus_v1.jsonl"
    assert digest == manifest["sha256"]


def test_frozen_corpus_with_skipped_lines_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real = load_ragtruth

    def one_line_skipped(path: Path, expected_sha256: str | None = None) -> LoadResult:
        result = real(path, expected_sha256)
        result.records.pop()
        result.skipped.append(SkippedRecord(5, "x", "invalid record: example"))
        return result

    monkeypatch.setattr(ragtruth, "load_ragtruth", one_line_skipped)
    with pytest.raises(IncompleteCorpusError, match="loaded 918 of 919"):
        load_corpus_v1()


def test_full_corpus_matches_manifest() -> None:
    manifest = json.loads(CORPUS_V1_MANIFEST.read_text(encoding="utf-8"))
    result = load_corpus_v1()
    assert result.skipped == []
    assert len(result.records) == manifest["record_count"]
    assert sum(r.metadata["gold_set"] for r in result.records) == manifest["gold_set_count"]
