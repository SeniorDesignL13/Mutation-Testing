"""Tests for the verbosity padding operator."""

from mtlj.operators.base import ExpectedDirection, OperatorFamily
from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator

TEXT = "The bridge opened in 1932."
CONTEXT = ["The Sydney Harbour Bridge is a steel arch bridge across the harbour."]


def test_metadata_is_preserving_unchanged() -> None:
    meta = VerbosityPaddingOperator.metadata
    assert meta.family is OperatorFamily.PRESERVING
    assert meta.expected_direction is ExpectedDirection.UNCHANGED


def test_original_text_is_kept_and_longer() -> None:
    result = VerbosityPaddingOperator().apply(TEXT, CONTEXT, seed=1)
    assert result.changed
    assert result.mutated_text.startswith(TEXT)
    assert len(result.mutated_text.split()) > len(TEXT.split())


def test_same_seed_is_reproducible() -> None:
    op = VerbosityPaddingOperator()
    assert (
        op.apply(TEXT, CONTEXT, seed=3).mutated_text == op.apply(TEXT, CONTEXT, seed=3).mutated_text
    )


def test_works_with_empty_context() -> None:
    result = VerbosityPaddingOperator().apply(TEXT, [], seed=1)
    assert result.changed
    assert result.details["source_phrases_available"] == 0
