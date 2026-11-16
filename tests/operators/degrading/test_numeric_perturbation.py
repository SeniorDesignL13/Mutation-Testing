"""Tests for the numeric perturbation operator."""

import re
from decimal import Decimal

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.operators.base import ExpectedDirection, OperatorFamily
from mtlj.operators.degrading.numeric_perturbation import (
    NumericPerturbationOperator,
    find_numbers,
)
from mtlj.operators.registry import get_operator

OP = NumericPerturbationOperator()
SEEDS = range(30)


def _apply(answer: str, context: list[str], intensity: float = 1.0, seed: int = 1):
    return OP.apply(answer, context, intensity=intensity, seed=seed)


def _sub(result) -> dict:
    (sub,) = result.details["substitutions"]
    return sub


def _value(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def test_metadata_is_degrading_decrease() -> None:
    meta = NumericPerturbationOperator.metadata
    assert meta.family is OperatorFamily.DEGRADING
    assert meta.expected_direction is ExpectedDirection.DECREASE
    assert meta.version == 1
    assert [p.name for p in meta.parameters] == ["relative_change", "date_shift"]


def test_registry_finds_it() -> None:
    assert isinstance(get_operator("numeric_perturbation"), NumericPerturbationOperator)


# Each kind of number

# (kind, answer, context, original, pattern the mutated answer must match)
KINDS = [
    (
        "integer",
        "The tower is 324 metres tall.",
        ["The tower is 324 metres tall."],
        "324",
        r"The tower is \d+ metres tall\.",
    ),
    (
        "integer",
        "The firm had 1,200 employees.",
        ["The firm had 1,200 employees."],
        "1,200",
        r"The firm had (\d{1,3}(,\d{3})+|\d{1,3}) employees\.",
    ),
    (
        "decimal",
        "The deal was worth $3.40 million.",
        ["The deal was worth $3.40 million."],
        "3.40",
        r"The deal was worth \$\d+\.\d\d million\.",
    ),
    (
        "percent",
        "Revenue grew 12.5% last year.",
        ["Revenue grew 12.5% last year."],
        "12.5",
        r"Revenue grew \d+\.\d% last year\.",
    ),
    (
        "percent",
        "Turnout was 64 percent.",
        ["Turnout was 64 percent."],
        "64",
        r"Turnout was \d+ percent\.",
    ),
    (
        "year",
        "The bridge opened in 1932.",
        ["The bridge opened in 1932."],
        "1932",
        r"The bridge opened in \d{4}\.",
    ),
    (
        "day",
        "The bridge opened on March 19.",
        ["The bridge opened on March 19."],
        "19",
        r"The bridge opened on March \d{1,2}\.",
    ),
    (
        "day",
        "The treaty was signed on 7 June.",
        ["The treaty was signed on 7 June."],
        "7",
        r"The treaty was signed on \d{1,2} June\.",
    ),
]


@pytest.mark.parametrize(("kind", "answer", "context", "original", "shape"), KINDS)
@pytest.mark.parametrize("intensity", [0.2, 1.0])
def test_changes_each_kind_and_keeps_its_format(
    kind, answer, context, original, shape, intensity
) -> None:
    for seed in SEEDS:
        result = _apply(answer, context, intensity=intensity, seed=seed)
        sub = _sub(result)
        assert result.changed
        assert sub["kind"] == kind
        assert sub["original"] == original
        assert _value(sub["replacement"]) != _value(original)
        assert re.fullmatch(shape, result.mutated_text), result.mutated_text
        # Same precision as the original.
        assert len(sub["replacement"].partition(".")[2]) == len(original.partition(".")[2])


def test_a_full_date_changes_its_year_or_its_day() -> None:
    answer = "The bridge opened on March 5, 1932."
    kinds = {_sub(_apply(answer, [answer], seed=seed))["kind"] for seed in SEEDS}
    assert kinds == {"day", "year"}


def test_keeps_thousands_separators() -> None:
    answer = "The stadium holds 45,000 people."
    for intensity in (0.2, 0.6):
        replacement = _sub(_apply(answer, [answer], intensity=intensity))["replacement"]
        assert re.fullmatch(r"\d{2},\d{3}", replacement)


def test_does_not_add_separators_the_original_lacked() -> None:
    answer = "The stadium holds 45000 people."
    replacement = _sub(_apply(answer, [answer], intensity=0.2))["replacement"]
    assert "," not in replacement


def test_keeps_units_and_symbols_around_the_number() -> None:
    answer = "It weighs 12 kg and costs €40."
    for seed in SEEDS:
        mutated = _apply(answer, [answer], seed=seed).mutated_text
        assert re.fullmatch(r"It weighs \d+ kg and costs €\d+\.", mutated)


# Magnitude


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        ("Sales reached 800 units.", "integer"),
        ("The rate was 6.00 percent.", "percent"),
        ("It was founded in 1950.", "year"),
    ],
)
def test_intensity_sets_how_far_the_number_moves(answer, kind) -> None:
    def size(intensity: float) -> float:
        sub = _sub(_apply(answer, [answer], intensity=intensity))
        assert sub["kind"] == kind
        return abs(sub["change"])

    assert size(0.2) < size(0.6) < size(1.0)


def test_relative_change_matches_intensity() -> None:
    answer = "Sales reached 800 units."
    assert abs(_sub(_apply(answer, [answer], intensity=1.0))["relative_change"]) == 0.5
    assert abs(_sub(_apply(answer, [answer], intensity=0.2))["relative_change"]) == 0.1


def test_years_shift_by_ceil_intensity_times_ten() -> None:
    answer = "It was founded in 1950."
    assert abs(_sub(_apply(answer, [answer], intensity=0.2))["change"]) == 2
    assert abs(_sub(_apply(answer, [answer], intensity=1.0))["change"]) == 10


def test_a_small_number_still_changes_after_rounding() -> None:
    """10% of 3 rounds back to 3, so the number moves one unit instead."""
    answer = "She has 3 cats."
    sub = _sub(_apply(answer, [answer], intensity=0.2))
    assert sub["replacement"] in {"2", "4"}


def test_zero_intensity_changes_nothing() -> None:
    answer = "The tower is 324 metres tall."
    result = _apply(answer, [answer], intensity=0.0)
    assert not result.changed
    assert result.mutated_text == answer


# Never a value that happens to be correct


def test_new_value_never_appears_in_the_context() -> None:
    answer = "The team scored 20 points."
    # 10 and 30 are the ±50% values, 18 and 22 the ±10% ones: all stated elsewhere.
    context = ["The team scored 20 points, after 10 and 30 in earlier games, 18 and 22 before."]
    context_values = {n.value for n in find_numbers(context[0])}
    for intensity in (0.2, 1.0):
        for seed in SEEDS:
            replacement = _sub(_apply(answer, context, intensity, seed))["replacement"]
            assert _value(replacement) not in context_values


def test_value_in_another_passage_is_also_avoided() -> None:
    answer = "It was founded in 1950."
    context = ["It was founded in 1950.", "A rival opened in 1940 and another in 1960."]
    for seed in SEEDS:
        replacement = _sub(_apply(answer, context, seed=seed))["replacement"]
        assert replacement not in {"1940", "1960"}


def test_a_value_written_differently_in_the_context_is_avoided() -> None:
    """1,500 in the context rules out 1500 in the answer, whatever the formatting."""
    answer = "The plant employs 1000 people."
    context = ["The plant employs 1000 people, up from 1,500 in 2010 and 500 before."]
    for seed in SEEDS:
        replacement = _sub(_apply(answer, context, seed=seed))["replacement"]
        assert replacement not in {"1500", "500"}


def test_value_stays_above_zero() -> None:
    answer = "There were 0 incidents and 1 warning."
    for seed in SEEDS:
        sub = _sub(_apply(answer, [answer], seed=seed))
        assert _value(sub["replacement"]) > 0


def test_percentage_up_to_100_stays_up_to_100() -> None:
    answer = "Turnout was 98 percent."
    for seed in SEEDS:
        assert _value(_sub(_apply(answer, [answer], seed=seed))["replacement"]) <= 100


def test_day_stays_a_day_every_month_has() -> None:
    answer = "It opened on February 26."
    for seed in SEEDS:
        assert 1 <= int(_sub(_apply(answer, [answer], seed=seed))["replacement"]) <= 28


def test_no_valid_replacement_means_no_change() -> None:
    """Every year the operator could reach is stated in the context."""
    answer = "It was founded in 1001."
    years = " ".join(str(y) for y in range(1000, 1100))
    result = _apply(answer, [answer + " " + years])
    assert not result.changed
    assert result.details["skipped"]["no_valid_replacement"] == 1


# Which numbers are targeted


def test_ignores_numbers_the_context_does_not_state() -> None:
    answer = "The tower is 324 metres tall and 125 years old."
    context = ["The tower is 324 metres tall."]
    for seed in SEEDS:
        result = _apply(answer, context, seed=seed)
        assert _sub(result)["original"] == "324"
        assert result.details["skipped"]["no_context_support"] == 1


def test_no_supported_number_means_no_change() -> None:
    answer = "The tower is 324 metres tall."
    result = _apply(answer, ["The tower is very tall."])
    assert not result.changed
    assert result.details["substitutions"] == []


def test_no_context_means_no_change() -> None:
    assert not _apply("The tower is 324 metres tall.", []).changed


def test_changes_exactly_one_number() -> None:
    answer = "In 1932 the bridge carried 6 lanes over 503 metres."
    for seed in SEEDS:
        result = _apply(answer, [answer], seed=seed)
        before = [n.text for n in find_numbers(answer)]
        after = [n.text for n in find_numbers(result.mutated_text)]
        assert sum(b != a for b, a in zip(before, after, strict=True)) == 1


def test_changes_only_one_occurrence_of_a_repeated_value() -> None:
    answer = "Team A scored 12. Team B also scored 12."
    for seed in SEEDS:
        assert _apply(answer, [answer], seed=seed).mutated_text.count("12") == 1


def test_prefers_a_value_that_appears_once() -> None:
    answer = "Team A scored 12, Team B scored 12, and 40 people watched."
    for seed in SEEDS:
        assert _sub(_apply(answer, [answer], seed=seed))["original"] == "40"


@pytest.mark.parametrize(
    "text",
    [
        "COVID-19",
        "the 3rd floor",
        "at 10:30",
        "on 2019-03-04",
        "a 3/4 share",
        "version v2.0.1",
        "codes 1,2,3",
        "the B2B market",
        "a 5km run",
    ],
)
def test_numbers_joined_to_other_text_are_left_alone(text) -> None:
    assert find_numbers(text) == []
    assert not _apply(text, [text]).changed


def test_finds_standalone_numbers_with_their_kinds() -> None:
    text = "On March 5, 1932, 1,200 people (45%) paid $3.50 each, and 7 stayed."
    found = [(n.text, n.kind) for n in find_numbers(text)]
    assert found == [
        ("5", "day"),
        ("1932", "year"),
        ("1,200", "integer"),
        ("45", "percent"),
        ("3.50", "decimal"),
        ("7", "integer"),
    ]


# Provenance and reproducibility


def test_provenance_points_at_the_changed_span() -> None:
    answer = "The tower is 324 metres tall."
    result = _apply(answer, [answer])
    sub = _sub(result)
    assert answer[sub["start"] : sub["end"]] == sub["original"] == "324"
    assert result.mutated_text[sub["start"] :].startswith(sub["replacement"])


def test_same_seed_is_reproducible() -> None:
    answer = "In 1932 the bridge carried 6 lanes over 503 metres."
    assert _apply(answer, [answer], seed=5) == _apply(answer, [answer], seed=5)


def test_different_seeds_can_change_different_numbers() -> None:
    answer = "In 1932 the bridge carried 6 lanes over 503 metres."
    originals = {_sub(_apply(answer, [answer], seed=s))["original"] for s in SEEDS}
    assert len(originals) > 1


def test_makes_a_reproducible_mutant() -> None:
    record = DatasetRecord(
        record_id="n_1",
        source_dataset="test",
        question="How tall is the tower?",
        context=[{"passage_id": "p1", "text": "The tower is 324 metres tall."}],
        answer="The tower is 324 metres tall.",
    )
    mutant = Mutant.generate(record, OP, intensity=0.6, seed=3)
    assert mutant.changed
    assert mutant.operator_name == "numeric_perturbation"
    assert mutant.operator_version == 1
    assert mutant.regenerate(record) == mutant
