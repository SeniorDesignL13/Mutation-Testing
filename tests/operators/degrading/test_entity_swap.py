"""Tests for the entity swap operator."""

from mtlj.operators.base import ExpectedDirection, OperatorFamily
from mtlj.operators.degrading.entity_swap import PLACE_POOL, EntitySwapOperator

TEXT = "The conference was held in Paris last spring."


def test_metadata_is_degrading_decrease() -> None:
    meta = EntitySwapOperator.metadata
    assert meta.family is OperatorFamily.DEGRADING
    assert meta.expected_direction is ExpectedDirection.DECREASE


def test_swaps_place_entity() -> None:
    result = EntitySwapOperator().apply(TEXT, context=[], seed=1)
    assert result.changed
    assert "Paris" not in result.mutated_text
    assert result.details["swapped_to"] in PLACE_POOL


def test_same_seed_is_reproducible() -> None:
    op = EntitySwapOperator()
    a = op.apply(TEXT, context=[], seed=7)
    b = op.apply(TEXT, context=[], seed=7)
    assert a.mutated_text == b.mutated_text


def test_no_entity_leaves_text_unchanged() -> None:
    text = "The soup needs more salt."
    result = EntitySwapOperator().apply(text, context=[], seed=1)
    assert not result.changed
    assert result.mutated_text == text
