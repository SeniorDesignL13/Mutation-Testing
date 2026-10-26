"""Tests for the entity swap operator, with fixtures for PERSON, ORG, GPE and DATE."""

import re

import pytest

from mtlj.operators.base import ExpectedDirection, OperatorFamily
from mtlj.operators.degrading.entity_swap import EntitySwapOperator

# (label, answer, context, original entity, replacements that the context offers)
FIXTURES = [
    (
        "PERSON",
        "Albert Einstein developed the theory of relativity.",
        ["Albert Einstein developed the theory of relativity. Isaac Newton described gravity."],
        "Albert Einstein",
        {"Isaac Newton"},
    ),
    (
        "ORG",
        "Microsoft released the report.",
        ["Microsoft released the report. Google and Amazon published separate reports."],
        "Microsoft",
        {"Google", "Amazon"},
    ),
    (
        "GPE",
        "The conference was held in Paris.",
        ["The conference took place in Paris. The previous meeting was in Berlin."],
        "Paris",
        {"Berlin"},
    ),
]
DATE_ANSWER = "The bridge opened on March 5, 1932."
DATE_CONTEXT = ["The Sydney Harbour Bridge opened on March 5, 1932. Construction began in 1923."]

MULTI_ANSWER = (
    "Alice Johnson lives in Seattle. She works for Microsoft. The firm was founded in 1975."
)
MULTI_CONTEXT = [
    "Alice Johnson lives in Seattle, near Portland. Bob Lee also works for Microsoft, "
    "a rival of Apple. Microsoft was founded in 1975."
]


def test_metadata_is_degrading_decrease() -> None:
    meta = EntitySwapOperator.metadata
    assert meta.family is OperatorFamily.DEGRADING
    assert meta.expected_direction is ExpectedDirection.DECREASE


@pytest.mark.parametrize(("label", "answer", "context", "original", "offered"), FIXTURES)
def test_swaps_each_entity_type(label, answer, context, original, offered) -> None:
    result = EntitySwapOperator().apply(answer, context, intensity=0.2, seed=1)
    assert result.changed
    sub = result.details["substitutions"][0]
    assert sub["label"] == label
    assert sub["original"] == original
    assert sub["replacement"] in offered
    assert sub["replacement_source"] == "context"
    assert original not in result.mutated_text
    assert sub["replacement"] in result.mutated_text


def test_swaps_a_date_and_keeps_its_format() -> None:
    result = EntitySwapOperator().apply(DATE_ANSWER, DATE_CONTEXT, intensity=0.2, seed=1)
    sub = result.details["substitutions"][0]
    assert sub["label"] == "DATE"
    assert re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", sub["replacement"])
    assert sub["replacement"] != "March 5, 1932"
    # The new year must not be a year the context mentions.
    assert "1923" not in sub["replacement"]
    assert result.mutated_text.startswith("The bridge opened on ")


def test_ignores_entities_the_context_does_not_support() -> None:
    result = EntitySwapOperator().apply(
        "The conference was held in Paris.", ["The meeting took place in Berlin."], seed=1
    )
    assert not result.changed
    assert result.mutated_text == "The conference was held in Paris."
    assert result.details["skipped"]["no_context_support"] >= 1


def test_no_context_means_no_change() -> None:
    result = EntitySwapOperator().apply("The conference was held in Paris.", [], seed=1)
    assert not result.changed


def test_replacement_is_never_supported_by_the_original_sentence() -> None:
    context = ["Paris is the capital of France. Berlin is in Germany."]
    for seed in range(30):
        result = EntitySwapOperator().apply("The trip ended in Paris.", context, seed=seed)
        assert result.details["substitutions"][0]["replacement"] != "France"


def test_changes_only_one_occurrence() -> None:
    answer = "Paris is large. Paris is also old."
    context = ["Paris is a large old city. Berlin is also large."]
    result = EntitySwapOperator().apply(answer, context, intensity=0.2, seed=1)
    assert result.changed
    assert result.mutated_text.count("Paris") == 1


def test_provenance_points_at_the_changed_span() -> None:
    result = EntitySwapOperator().apply(MULTI_ANSWER, MULTI_CONTEXT, intensity=1.0, seed=3)
    assert result.details["substitutions"]
    for sub in result.details["substitutions"]:
        assert MULTI_ANSWER[sub["start"] : sub["end"]] == sub["original"]
        assert sub["original"] in sub["claim_sentence"]
        assert sub["claim_sentence"] in MULTI_ANSWER
        assert sub["replacement_source"] in {"context", "pool", "date_shift"}


def test_intensity_controls_number_of_substitutions() -> None:
    op = EntitySwapOperator()
    counts = [
        len(op.apply(MULTI_ANSWER, MULTI_CONTEXT, intensity=i, seed=5).details["substitutions"])
        for i in (0.2, 0.4, 1.0)
    ]
    assert counts[0] == 1
    assert counts == sorted(counts)
    assert counts[-1] >= 2


def test_each_claim_is_changed_at_most_once() -> None:
    result = EntitySwapOperator().apply(MULTI_ANSWER, MULTI_CONTEXT, intensity=1.0, seed=9)
    sentences = [s["claim_sentence_index"] for s in result.details["substitutions"]]
    assert len(sentences) == len(set(sentences))


def test_zero_intensity_changes_nothing() -> None:
    result = EntitySwapOperator().apply(MULTI_ANSWER, MULTI_CONTEXT, intensity=0.0, seed=1)
    assert not result.changed
    assert result.mutated_text == MULTI_ANSWER


def test_same_seed_is_reproducible() -> None:
    op = EntitySwapOperator()
    a = op.apply(MULTI_ANSWER, MULTI_CONTEXT, intensity=1.0, seed=11)
    b = op.apply(MULTI_ANSWER, MULTI_CONTEXT, intensity=1.0, seed=11)
    assert a.mutated_text == b.mutated_text
    assert a.details == b.details
