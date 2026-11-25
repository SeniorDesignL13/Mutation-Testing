"""Tests for the validity filter: broken degrading mutants are rejected, wrong-but-readable kept."""

import json
from pathlib import Path

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.validity import (
    DUPLICATE,
    GRAMMAR,
    NONSENSICAL_ENTITY,
    SELF_CONTRADICTION,
    Problem,
    check_mutant,
    check_text,
)
from mtlj.operators.registry import get_operator

GOLD_SET_MUTANTS = Path(__file__).parents[1] / "fixtures" / "gold-set" / "mutants.jsonl"


def _swap(
    text: str, original: str, replacement: str, *, label: str | None = None, kind: str | None = None
) -> tuple[str, list[dict]]:
    """The mutated text and substitutions for replacing the first ``original`` in ``text``."""
    start = text.index(original)
    end = start + len(original)
    substitution = {"original": original, "replacement": replacement, "start": start, "end": end}
    if label:
        substitution["label"] = label
    if kind:
        substitution["kind"] = kind
    return text[:start] + replacement + text[end:], [substitution]


def _problems(text: str, original: str, replacement: str, **kw) -> list[Problem]:
    mutated, substitutions = _swap(text, original, replacement, **kw)
    return check_text(text, mutated, substitutions)


# (text, original, replacement, label or kind, the check that must fail)
BROKEN = [
    # grammar
    ("It was debated in the Senate.", "Senate", "the House", {"label": "ORG"}, GRAMMAR),
    ("Acme makes anvils.", "Acme", "Acme (UK", {"label": "ORG"}, GRAMMAR),
    ("Shares of Acme rose.", "Acme", " Acme", {"label": "ORG"}, GRAMMAR),
    ("She has 1 cat at home.", "1", "2", {"kind": "integer"}, GRAMMAR),
    ("The trip took 3 days in total.", "3", "1", {"kind": "integer"}, GRAMMAR),
    (
        '"The Hobbit" is a children\'s book.',
        "Hobbit",
        "Harbor Analytics",
        {"label": "ORG"},
        GRAMMAR,
    ),
    # duplicate
    ("Paris and Berlin hosted the games.", "Paris", "Berlin", {"label": "GPE"}, DUPLICATE),
    ("Between 10 and 20 people came.", "20", "10", {"kind": "integer"}, DUPLICATE),
    # nonsensical entity
    (
        "Mix in the Worcestershire sauce.",
        "Worcestershire",
        "James",
        {"label": "PERSON"},
        NONSENSICAL_ENTITY,
    ),
    (
        "Make sure the uTorrent program is closed.",
        "uTorrent",
        "Scan Leftovers",
        {"label": "ORG"},
        NONSENSICAL_ENTITY,
    ),
    ("Pat the salmon dry with a towel.", "Pat", "Thomas", {"label": "PERSON"}, NONSENSICAL_ENTITY),
    (
        "Preheat the oven. Pat the salmon dry.",
        "Pat",
        "Thomas",
        {"label": "PERSON"},
        NONSENSICAL_ENTITY,
    ),
    ("Frodo is Bilbo's nephew.", "Bilbo", "Alder Partners", {"label": "ORG"}, NONSENSICAL_ENTITY),
    (
        "The story ends with their battle with Smaug the dragon.",
        "Smaug",
        "Rome",
        {"label": "GPE"},
        NONSENSICAL_ENTITY,
    ),
    # self-contradiction
    ("The war lasted from 1939 to 1945.", "1939", "1946", {"kind": "year"}, SELF_CONTRADICTION),
    ("Between 30 and 50 people came.", "30", "60", {"kind": "integer"}, SELF_CONTRADICTION),
    ("60% voted yes and 40% voted no.", "60", "90", {"kind": "percent"}, SELF_CONTRADICTION),
]

# Wrong but readable: a fair test of the judge, so these must be kept.
VALID = [
    ("The bridge opened in 1932.", "1932", "1930", {"kind": "year"}),
    ("The colonists responded to the Stamp Act of 1765.", "1765", "1760", {"label": "DATE"}),
    ("Windows will display a temporary area.", "Windows", "Vista", {"label": "ORG"}),
    ("The conference was held in Paris.", "Paris", "Berlin", {"label": "GPE"}),
    ("Einstein visited the Paris office.", "Paris", "Berlin", {"label": "GPE"}),
    ("The Microsoft CEO spoke first.", "Microsoft", "Google", {"label": "ORG"}),
    ("We arrived in Paris the next day.", "Paris", "Rome", {"label": "GPE"}),
    ("John the Baptist preached by the river.", "John", "Thomas", {"label": "PERSON"}),
    ("Frodo is Bilbo's nephew.", "Bilbo", "Samwise", {"label": "PERSON"}),
    ("The tower is 324 metres tall.", "324", "292", {"kind": "integer"}),
    ("She has 2 cats at home.", "2", "3", {"kind": "integer"}),
    ("She has 1.5 kilograms of rice.", "1.5", "2.5", {"kind": "decimal"}),
    ("The war lasted from 1939 to 1945.", "1939", "1941", {"kind": "year"}),
    ("Between 10 and 20 people came.", "20", "30", {"kind": "integer"}),
    ("60% voted yes and 40% voted no.", "60", "50", {"kind": "percent"}),
    ("Growth was 30% and the margin 80%.", "30", "45", {"kind": "percent"}),
    # The original was already untidy there: the swap isn't blamed for it.
    ("I bought the the watch in Paris.", "Paris", "Berlin", {"label": "GPE"}),
    # The replacement is in another sentence, not the same one.
    ("Berlin is large. The games were in Paris.", "Paris", "Berlin", {"label": "GPE"}),
]


@pytest.mark.parametrize(("text", "original", "replacement", "kw", "check"), BROKEN)
def test_broken_mutants_are_rejected(text, original, replacement, kw, check) -> None:
    problems = _problems(text, original, replacement, **kw)
    assert check in {p.check for p in problems}, problems


@pytest.mark.parametrize(("text", "original", "replacement", "kw"), VALID)
def test_wrong_but_readable_mutants_are_kept(text, original, replacement, kw) -> None:
    assert _problems(text, original, replacement, **kw) == []


def test_a_problem_names_the_check_the_reason_and_the_mutated_sentence() -> None:
    [problem] = _problems(
        "Preheat the oven. Mix in the Worcestershire sauce.",
        "Worcestershire",
        "James",
        label="PERSON",
    )
    assert problem.check == NONSENSICAL_ENTITY
    assert "James" in problem.explanation and "sauce" in problem.explanation
    assert problem.sentence == "Mix in the James sauce."
    assert problem.as_dict() == {
        "check": NONSENSICAL_ENTITY,
        "explanation": problem.explanation,
        "sentence": "Mix in the James sauce.",
    }


def test_every_swap_in_a_mutant_is_checked() -> None:
    text = "Paris hosted it in 1990. Mix in the Worcestershire sauce."
    subs = [
        {"original": "Paris", "replacement": "Rome", "start": 0, "end": 5, "label": "GPE"},
        {
            "original": "Worcestershire",
            "replacement": "James",
            "start": text.index("Worcestershire"),
            "end": text.index("Worcestershire") + len("Worcestershire"),
            "label": "PERSON",
        },
    ]
    mutated = "Rome hosted it in 1990. Mix in the James sauce."
    [problem] = check_text(text, mutated, subs)
    # The sentence is found in the mutant even though an earlier swap changed its length.
    assert problem.sentence == "Mix in the James sauce."


def _subs(text: str, *swaps: tuple[str, str, dict]) -> list[dict]:
    """Substitutions for several swaps in ``text``, each at the first match of its original."""
    subs = []
    for original, replacement, kw in swaps:
        start = text.index(original)
        subs.append(
            {
                "original": original,
                "replacement": replacement,
                "start": start,
                "end": start + len(original),
                **kw,
            }
        )
    return subs


def test_two_swaps_that_trade_names_are_not_duplicates() -> None:
    text = "She flew from Paris to London in May."
    gpe = {"label": "GPE"}
    subs = _subs(text, ("Paris", "London", gpe), ("London", "Paris", gpe))
    assert check_text(text, "She flew from London to Paris in May.", subs) == []


def test_two_swaps_that_shift_numbers_are_not_duplicates() -> None:
    text = "Prices rose from 10 to 20 dollars."
    num = {"kind": "integer"}
    subs = _subs(text, ("10", "20", num), ("20", "30", num))
    assert check_text(text, "Prices rose from 20 to 30 dollars.", subs) == []


def test_substitutions_that_dont_say_where_they_are_are_ignored() -> None:
    text = "Mix in the Worcestershire sauce."
    mutated = "Mix in the James sauce."
    assert check_text(text, mutated, [{"original": "Worcestershire", "replacement": "James"}]) == []
    # Positions that don't match the original text aren't trusted either.
    wrong = {"original": "Worcestershire", "replacement": "James", "start": 0, "end": 14}
    assert check_text(text, mutated, [wrong]) == []


# -- check_mutant ----------------------------------------------------------------------------


def _record(answer: str, context: str) -> DatasetRecord:
    return DatasetRecord(
        record_id="r1",
        source_dataset="test",
        question="q",
        context=[{"passage_id": "p1", "text": context}],
        answer=answer,
    )


def test_check_mutant_rejects_a_broken_numeric_mutant() -> None:
    record = _record("She has 1 cat.", "She has 1 cat and the vet says 1 cat is enough.")
    mutant = Mutant.generate(record, get_operator("numeric_perturbation"), 1.0, seed=0)
    assert mutant.mutated_text == "She has 2 cat."
    [problem] = check_mutant(mutant)
    assert problem.check == GRAMMAR


def test_check_mutant_keeps_a_clean_numeric_mutant() -> None:
    record = _record("The tower is 324 metres tall.", "The tower is 324 metres tall.")
    mutant = Mutant.generate(record, get_operator("numeric_perturbation"), 0.2, seed=0)
    assert mutant.changed
    assert check_mutant(mutant) == []


def test_preserving_and_unchanged_mutants_are_never_checked() -> None:
    record = _record("Mix in the Worcestershire sauce.", "Mix in the Worcestershire sauce.")
    bullets = Mutant.generate(record, get_operator("reformat_to_bullets"), 1.0, seed=0)
    assert check_mutant(bullets) == []
    unchanged = Mutant.generate(record, get_operator("numeric_perturbation"), 1.0, seed=0)
    assert not unchanged.changed
    assert check_mutant(unchanged) == []


# -- real mutants ----------------------------------------------------------------------------

# Every distinct entity swap in the gold-set fixtures, by (original, replacement), and whether
# it's broken. The two misses are known: "File and Vista" is split by spaCy into two names that
# look like "Paris and London", and "representation in the Red Coats" is odd but readable.
GOLD_SET_SWAPS = {
    ("LCD", "Windex"): True,
    ("Deoxyribonucleic", "James"): True,
    ("Pat", "Thomas"): True,
    ("Worcestershire", "James"): True,
    ("uTorrent", "Scan Leftovers"): True,
    ("Linksys", "Wireless"): True,
    ("Bilbo", "Alder Partners"): True,
    ("Bilbo", "Harbor Analytics"): True,
    ("Hobbit", "Harbor Analytics"): True,
    ("Smaug", "Rome"): True,
    ("2010", "2016"): False,
    ("1765", "1760"): False,
    ("1765", "1764"): False,
    ("Windows", "Vista"): False,
    ("Folder Tasks", "Vista"): False,  # broken, but not caught
    ("Parliament", "the Red Coats"): False,  # borderline, kept
}


def test_real_gold_set_swaps_are_judged_as_expected() -> None:
    judged = {}
    with GOLD_SET_MUTANTS.open(encoding="utf-8") as lines:
        for line in lines:
            mutant = json.loads(line)
            if mutant["operator_name"] != "entity_swap":
                continue
            for sub in mutant["details"]["substitutions"]:
                start, end = sub["start"], sub["end"]
                mutated = (
                    mutant["original_text"][:start]
                    + sub["replacement"]
                    + mutant["original_text"][end:]
                )
                problems = check_text(mutant["original_text"], mutated, [sub])
                judged[(sub["original"], sub["replacement"])] = bool(problems)
    assert judged == GOLD_SET_SWAPS
