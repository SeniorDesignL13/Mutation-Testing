"""Tests for verbosity padding at the 50/100/200/400-word study levels."""

import re

import pytest

from mtlj.operators.preserving.verbosity_padding import (
    MENTION_TEMPLATES,
    NEUTRAL_CONNECTORS,
    NEUTRAL_STEMS,
    PAD_LEVELS,
    TOLERANCE,
    VerbosityPaddingOperator,
    intensity_for_words,
)

ANSWER = (
    "To bake salmon medium rare, heat the oven to 275 degrees and bake the fillet "
    "for about 15 minutes. Season it with salt and a little lemon juice first. "
    "The fish is done when the center is still slightly translucent."
)
CONTEXT = [
    "Salmon fillets cook gently in a low oven, which keeps the flesh moist and tender.",
    "A medium rare fillet has a translucent center and flakes easily with a fork.",
    "Lemon juice and sea salt are a simple seasoning for baked salmon.",
]


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z']+", s.lower())


@pytest.mark.parametrize("level", PAD_LEVELS)
def test_adds_target_words_within_tolerance(level: int) -> None:
    result = VerbosityPaddingOperator().apply(
        ANSWER, CONTEXT, intensity=intensity_for_words(level), seed=1
    )
    added = len(result.mutated_text.split()) - len(ANSWER.split())
    assert result.details["target_words"] == level
    assert abs(added - level) <= TOLERANCE * level
    assert result.details["words_added"] == added
    assert result.details["within_tolerance"]


@pytest.mark.parametrize("level", PAD_LEVELS)
def test_levels_work_without_context(level: int) -> None:
    result = VerbosityPaddingOperator().apply(
        ANSWER, [], intensity=intensity_for_words(level), seed=2
    )
    added = len(result.mutated_text.split()) - len(ANSWER.split())
    assert abs(added - level) <= TOLERANCE * level


@pytest.mark.parametrize("level", PAD_LEVELS)
def test_original_answer_is_kept_unchanged(level: int) -> None:
    result = VerbosityPaddingOperator().apply(
        ANSWER, CONTEXT, intensity=intensity_for_words(level), seed=3
    )
    assert result.mutated_text.startswith(ANSWER)


@pytest.mark.parametrize("level", PAD_LEVELS)
def test_no_new_claims_or_numbers(level: int) -> None:
    """Every filler word comes from the fixed filler lists or the source context."""
    result = VerbosityPaddingOperator().apply(
        ANSWER, CONTEXT, intensity=intensity_for_words(level), seed=4
    )
    filler = result.mutated_text[len(ANSWER.rstrip()) :]
    allowed = set(_words(" ".join(NEUTRAL_CONNECTORS + NEUTRAL_STEMS + MENTION_TEMPLATES)))
    allowed |= set(_words(" ".join(CONTEXT)))
    assert not any(ch.isdigit() for ch in filler)
    assert set(_words(filler)) <= allowed


def test_same_seed_is_reproducible_and_seeds_differ() -> None:
    op = VerbosityPaddingOperator()
    a = op.apply(ANSWER, CONTEXT, intensity=0.5, seed=7)
    b = op.apply(ANSWER, CONTEXT, intensity=0.5, seed=7)
    c = op.apply(ANSWER, CONTEXT, intensity=0.5, seed=8)
    assert a.mutated_text == b.mutated_text
    assert a.mutated_text != c.mutated_text


def test_filler_is_not_one_sentence_repeated() -> None:
    result = VerbosityPaddingOperator().apply(ANSWER, CONTEXT, intensity=1.0, seed=5)
    filler = result.mutated_text[len(ANSWER.rstrip()) :]
    sentences = [s.strip() for s in re.split(r"(?<=\.)\s+", filler) if s.strip()]
    assert len(set(sentences)) >= 0.9 * len(sentences)


def test_more_intensity_means_more_words() -> None:
    op = VerbosityPaddingOperator()
    lengths = [
        len(
            op.apply(ANSWER, CONTEXT, intensity=intensity_for_words(n), seed=1).mutated_text.split()
        )
        for n in PAD_LEVELS
    ]
    assert lengths == sorted(lengths)
    assert len(set(lengths)) == len(lengths)


def test_zero_intensity_changes_nothing() -> None:
    result = VerbosityPaddingOperator().apply(ANSWER, CONTEXT, intensity=0.0, seed=1)
    assert not result.changed
    assert result.mutated_text == ANSWER


def test_intensity_for_words() -> None:
    assert [intensity_for_words(n) for n in PAD_LEVELS] == [0.125, 0.25, 0.5, 1.0]


def test_every_filler_sentence_starts_with_a_capital() -> None:
    result = VerbosityPaddingOperator().apply(ANSWER, CONTEXT, intensity=1.0, seed=6)
    filler = result.mutated_text[len(ANSWER.rstrip()) :].strip()
    sentences = [s for s in re.split(r"(?<=\.)\s+", filler) if s]
    assert all(s[0].isupper() for s in sentences)


def test_nothing_added_leaves_the_text_untouched() -> None:
    """At intensity 0 no filler is added, so a trailing newline must not count as a change."""
    answer = "The fish is done when the center is translucent.\n"
    result = VerbosityPaddingOperator().apply(answer, CONTEXT, intensity=0.0, seed=1)
    assert result.changed is False
    assert result.mutated_text == answer
