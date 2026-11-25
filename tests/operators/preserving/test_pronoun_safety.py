"""Tests for the pronoun-reference safety check that guards sentence reordering (MTLJ-80)."""

import json
from pathlib import Path

import pytest

from mtlj.operators.preserving.pronoun_safety import (
    ReorderSafety,
    SentenceSafety,
    check_backward_references,
    exclusion_report,
)

CASES = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "pronoun_safety" / "cases.json").read_text("utf-8")
)
IDS = [case["name"] for case in CASES]


def safety(*reasons: tuple[str, ...]) -> ReorderSafety:
    """A ReorderSafety built by hand, one sentence per argument, without spaCy."""
    return ReorderSafety(
        tuple(SentenceSafety(i, f"Sentence {i}.", tuple(r)) for i, r in enumerate(reasons))
    )


# ------------------------------------------------------------------ the fixture set (spaCy)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_sentences_that_refer_back_are_flagged(case):
    result = check_backward_references(case["text"])
    flagged = {s.index for s in result.sentences if s.refers_back}

    assert set(case["refers_back"]) <= flagged, [s.reasons for s in result.sentences]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_fixture_independent_sentences_are_not_flagged(case):
    result = check_backward_references(case["text"])
    flagged = {s.index for s in result.sentences if s.refers_back}

    assert not flagged & set(case["independent"]), [s.reasons for s in result.sentences]


@pytest.mark.parametrize("case", [c for c in CASES if c.get("kind")], ids=lambda c: c["name"])
def test_fixture_reason_names_the_kind_of_reference(case):
    result = check_backward_references(case["text"])
    reasons = [r for i in case["refers_back"] for r in result.sentences[i].reasons]

    assert any(r.startswith(case["kind"]) for r in reasons), reasons


@pytest.mark.parametrize("case", [c for c in CASES if "movable" in c], ids=lambda c: c["name"])
def test_fixture_movable_sentences(case):
    assert list(check_backward_references(case["text"]).movable) == case["movable"]


@pytest.mark.parametrize("case", [c for c in CASES if "blocks" in c], ids=lambda c: c["name"])
def test_fixture_blocks_keep_a_chain_together(case):
    blocks = check_backward_references(case["text"]).blocks

    assert [list(b) for b in blocks] == case["blocks"]


def test_every_fixture_sentence_is_classified():
    for case in CASES:
        n = len(check_backward_references(case["text"]).sentences)
        assert sorted(case["refers_back"] + case["independent"]) == list(range(n)), case["name"]


def test_whitespace_only_answer_has_no_sentences():
    result = check_backward_references("   \n ")

    assert result.sentences == ()
    assert result.exclusion_rate is None


# ------------------------------------------------------------------ grouping logic (no spaCy)


def test_referring_sentence_joins_the_one_before():
    result = safety((), ("pronoun 'It'",), ())

    assert result.blocks == ((0, 1), (2,))
    assert result.movable == (2,)
    assert result.excluded == (0, 1)


def test_chain_of_references_is_one_block():
    result = safety((), ("pronoun 'It'",), ("linking 'then'",), (), ("demonstrative 'This'",))

    assert result.blocks == ((0, 1, 2), (3, 4))
    assert result.movable == ()


def test_first_sentence_that_refers_back_is_pinned_not_grouped():
    result = safety(("pronoun 'It'",), (), ())

    assert result.blocks == ((0,), (1,), (2,))
    assert result.movable == (1, 2)
    assert result.excluded == (0,)


def test_exclusion_rate_is_share_of_sentences_that_cant_move_alone():
    assert safety((), ("pronoun 'It'",), (), ()).exclusion_rate == pytest.approx(0.5)
    assert safety((), (), ()).exclusion_rate == 0.0
    assert safety().exclusion_rate is None


def test_summary_is_json_ready_and_explains_each_exclusion():
    summary = safety((), ("pronoun 'It'", "anaphoric 'also'"), ()).summary()

    assert json.loads(json.dumps(summary)) == summary
    assert summary["movable"] == [2]
    assert summary["excluded"] == [0, 1]
    assert summary["blocks"] == [[0, 1], [2]]
    assert summary["exclusion_rate"] == pytest.approx(2 / 3)
    assert summary["references"] == {"1": ["pronoun 'It'", "anaphoric 'also'"]}


def test_exclusion_report_pools_sentences_and_averages_answers():
    report = exclusion_report(
        [
            safety((), ("pronoun 'It'",)),  # 2 of 2 excluded, no movable pair
            safety((), (), (), ()),  # 0 of 4 excluded
            safety(),  # empty: counted as an answer, no rate
        ]
    )

    assert report == {
        "answers": 3,
        "sentences": 6,
        "excluded": 2,
        "sentence_rate": pytest.approx(2 / 6),
        "mean_answer_rate": pytest.approx(0.5),
        "no_movable_pairs": 2,
    }


def test_exclusion_report_of_nothing():
    report = exclusion_report([])

    assert report["answers"] == 0
    assert report["sentence_rate"] is None
    assert report["mean_answer_rate"] is None
