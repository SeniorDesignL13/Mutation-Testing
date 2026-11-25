"""Tests for the negation insertion operator."""

import json
import re
from pathlib import Path

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.operators.base import ExpectedDirection, OperatorFamily
from mtlj.operators.degrading.negation_insertion import NegationInsertionOperator
from mtlj.operators.registry import get_operator

OP = NegationInsertionOperator()
SEEDS = range(20)
GOLD_SET_RECORDS = Path(__file__).parents[2] / "fixtures" / "gold-set" / "records.jsonl"


def _negate(text: str, context: list[str] | None = None, intensity: float = 1.0, seed: int = 1):
    """Negate ``text``. Its context is itself unless given, so every claim is supported."""
    return OP.apply(text, [text] if context is None else context, intensity=intensity, seed=seed)


def test_metadata_is_degrading_decrease() -> None:
    meta = NegationInsertionOperator.metadata
    assert meta.name == "negation_insertion"
    assert meta.family is OperatorFamily.DEGRADING
    assert meta.expected_direction is ExpectedDirection.DECREASE
    assert meta.version == 1
    assert [p.name for p in meta.parameters] == ["num_negations"]


def test_is_registered() -> None:
    assert isinstance(get_operator("negation_insertion"), NegationInsertionOperator)


# -- where "not" goes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # a helper verb takes the "not"
        ("The museum was built in 1901.", "The museum was not built in 1901."),
        ("She has been the CEO since 2010.", "She has not been the CEO since 2010."),
        ("The plan will reduce costs.", "The plan will not reduce costs."),
        ("The turkey should rest for an hour.", "The turkey should not rest for an hour."),
        ("Users can reset the router.", "Users cannot reset the router."),
        ("The engine is running smoothly.", "The engine is not running smoothly."),
        # "be" as the main verb
        ("The tower is 324 metres tall.", "The tower is not 324 metres tall."),
        ("The tomatoes were ripe in June.", "The tomatoes were not ripe in June."),
        ("There are five rooms in the house.", "There are not five rooms in the house."),
        ("It's 5 km long.", "It's not 5 km long."),
        # any other verb takes "do", in the same tense and person
        ("The bridge opened in 1932.", "The bridge did not open in 1932."),
        ("The software runs on Linux.", "The software does not run on Linux."),
        ("Most cats sleep during the day.", "Most cats do not sleep during the day."),
        ("The company has three offices.", "The company does not have three offices."),
        ("He went to Paris in 1990.", "He did not go to Paris in 1990."),
        # a "get" passive takes "do" too, not "got not married"
        ("They got married in 2001.", "They did not get married in 2001."),
        ("The roads get closed every winter.", "The roads do not get closed every winter."),
        # contracted helpers are spelled out
        ("You'll need an adapter cable.", "You will not need an adapter cable."),
        ("They've finished the bridge.", "They have not finished the bridge."),
    ],
)
def test_negation_goes_where_english_puts_it(text: str, expected: str) -> None:
    result = _negate(text)
    assert result.changed
    assert result.mutated_text == expected


def test_substitution_records_where_and_how() -> None:
    text = "The bridge opened in 1932."
    result = _negate(text)
    [sub] = result.details["substitutions"]
    assert text[sub["start"] : sub["end"]] == sub["original"] == "opened"
    assert sub["replacement"] == "did not open"
    assert sub["form"] == "do"
    assert sub["verb"] == "opened"
    assert sub["claim_sentence"] == text
    assert sub["claim_sentence_index"] == 0


def test_substitutions_rebuild_the_mutant() -> None:
    text = "The bridge opened in 1932. It is 503 metres long. Engineers can inspect it."
    result = _negate(text, intensity=1.0)
    rebuilt = text
    for sub in reversed(result.details["substitutions"]):
        assert text[sub["start"] : sub["end"]] == sub["original"]
        rebuilt = rebuilt[: sub["start"]] + sub["replacement"] + rebuilt[sub["end"] :]
    assert rebuilt == result.mutated_text


# -- never stacks on a negation --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The bridge did not open in 1932.",
        "The bridge didn't open in 1932.",
        "The tower isn't very tall.",
        "He never visited Paris.",
        "No trains run on Sundays.",
        "Nothing was left in the house.",
        "Users cannot reset the router.",
        "The cake is made without eggs.",
        "Neither option works well.",
        "The plan will no longer reduce costs.",
    ],
)
def test_already_negated_sentences_are_left_alone(text: str) -> None:
    for seed in SEEDS:
        result = _negate(text, seed=seed)
        assert not result.changed
        assert result.details["skipped"]["already_negated"] == 1


def test_negating_twice_never_stacks() -> None:
    text = "The bridge opened in 1932. The tower is 324 metres tall."
    once = _negate(text).mutated_text
    twice = OP.apply(once, [text], intensity=1.0, seed=2)
    assert not twice.changed


def test_only_the_unnegated_sentence_is_changed() -> None:
    text = "The bridge did not open in 1932. The tower is 324 metres tall."
    for seed in SEEDS:
        result = _negate(text, seed=seed)
        assert result.mutated_text == (
            "The bridge did not open in 1932. The tower is not 324 metres tall."
        )


# -- only claims, and only supported ones ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Is the tower tall?",  # a question
        "Pat the salmon dry with a towel.",  # a command
        "Preheat the oven to 350 degrees.",
        "A short walk to the station.",  # no verb
        "You may also add vegetables.",  # "may not" doesn't contradict "may"
        "The storm might reach the coast.",
        "He'd left before noon.",  # "had" or "would"?
    ],
)
def test_non_claims_are_left_alone(text: str) -> None:
    result = _negate(text)
    assert not result.changed
    assert result.details["skipped"]["not_a_claim"] == 1


def test_claims_the_context_does_not_state_are_left_alone() -> None:
    result = _negate(
        "The bridge opened in 1932.", context=["Penguins live in Antarctica and eat krill."]
    )
    assert not result.changed
    assert result.details["skipped"]["no_context_support"] == 1


def test_a_supported_claim_is_chosen_over_an_unsupported_one() -> None:
    text = "The bridge opened in 1932. Penguins eat krill."
    for seed in SEEDS:
        result = _negate(text, context=["The bridge opened to traffic in 1932."], seed=seed)
        assert result.mutated_text == "The bridge did not open in 1932. Penguins eat krill."


# -- intensity -------------------------------------------------------------------------------

FIVE_CLAIMS = (
    "The bridge opened in 1932. It is 503 metres long. Engineers inspect it every year. "
    "The deck was painted in 2010. Tourists can walk across it."
)


@pytest.mark.parametrize(("intensity", "negations"), [(0.2, 1), (0.6, 3), (1.0, 5)])
def test_intensity_sets_how_many_claims_are_negated(intensity: float, negations: int) -> None:
    for seed in SEEDS:
        result = _negate(FIVE_CLAIMS, intensity=intensity, seed=seed)
        assert len(result.details["substitutions"]) == negations
        assert result.details["requested_negations"] == negations
        assert result.mutated_text.count(" not ") + result.mutated_text.count("cannot") == (
            negations
        )


def test_at_most_one_negation_per_sentence() -> None:
    text = "The bridge opened in 1932 and it carries trains."
    result = _negate(text, intensity=1.0)
    assert len(result.details["substitutions"]) == 1


def test_intensity_zero_changes_nothing() -> None:
    result = _negate(FIVE_CLAIMS, intensity=0.0)
    assert not result.changed
    assert result.mutated_text == FIVE_CLAIMS


def test_fewer_claims_than_requested_negates_them_all() -> None:
    result = _negate("The bridge opened in 1932.", intensity=1.0)
    assert len(result.details["substitutions"]) == 1
    assert result.details["requested_negations"] == 5


def test_same_seed_gives_the_same_mutant() -> None:
    first = _negate(FIVE_CLAIMS, intensity=0.2, seed=7)
    assert _negate(FIVE_CLAIMS, intensity=0.2, seed=7).mutated_text == first.mutated_text


def test_seed_chooses_which_claim_is_negated() -> None:
    picks = {_negate(FIVE_CLAIMS, intensity=0.2, seed=s).mutated_text for s in SEEDS}
    assert len(picks) > 1


# -- real answers ----------------------------------------------------------------------------


def _gold_set_records() -> list[DatasetRecord]:
    with GOLD_SET_RECORDS.open(encoding="utf-8") as lines:
        return [DatasetRecord.model_validate(json.loads(line)) for line in lines]


def test_real_answers_get_one_negation_per_claim_and_never_a_double_negative() -> None:
    negated = 0
    for record in _gold_set_records():
        mutant = Mutant.generate(record, OP, 1.0, seed=3)
        assert mutant.regenerate(record) == mutant  # reproducible from its provenance
        for sub in mutant.details["substitutions"]:
            negated += 1
            sentence = sub["claim_sentence"]
            assert not re.search(r"\b(not|n't|never)\b", sentence)
            assert "not not" not in mutant.mutated_text
    assert negated > 0
