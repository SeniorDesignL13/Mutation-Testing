"""Validity filter: reject degrading mutants that are broken rather than merely wrong.

A degrading mutant should read like a normal answer with one wrong fact in it. When a swap
breaks the sentence instead ("Mix in the James sauce", "She has 2 cat"), a judge can lower its
score because the text is odd, not because it checked the source, and that would be counted as
a detection. ``check_mutant`` finds those mutants, and ``generate_mutants`` skips them with
reason ``invalid`` before they can reach an audit.

Every check compares the mutant with its original and looks only at what the substitution
changed, so an answer that was already untidy isn't blamed on the operator. The checks are
deliberately conservative: rejecting a good mutant also skews results, so each one fires only
on a strong clue. A rejection lists every problem found:

* ``grammar``: a doubled word at the edge of the swap ("the the"), unbalanced brackets or
  quotes or stray spacing in the replacement, a number that no longer agrees with its noun
  ("2 cat", "1 days"), or a word replaced inside a quoted title ('"The Northwind Holdings"').
* ``duplicate``: the new name or number already appears in the same sentence ("Berlin and
  Berlin", "between 10 and 10").
* ``nonsensical_entity``: a name put where it can't fit, which is how spaCy's wrong labels
  show up. A person or company describing a common noun ("James sauce", "Windex screen"), a
  person's name in place of the verb that starts an instruction ("Thomas the salmon dry"), a
  company or place with relatives ("Alder Partners's nephew") or an epithet ("Rome the
  dragon").
* ``self_contradiction``: the sentence now contradicts itself, so it's wrong without reading
  the source: a reversed range ("from 1946 to 1945", "between 30 and 20") or percentages that
  added up to 100 now adding up to more.

Only substitutions that record where they are (``start``, ``end``, ``original`` and
``replacement`` in ``details["substitutions"]``, as entity swap and numeric perturbation do)
are checked. A degrading operator without them passes unchecked.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import pairwise
from typing import Any

from mtlj.data.mutants import Mutant
from mtlj.operators.base import OperatorFamily
from mtlj.operators.degrading.entity_swap import _nlp
from mtlj.operators.degrading.numeric_perturbation import find_numbers

GRAMMAR = "grammar"
DUPLICATE = "duplicate"
NONSENSICAL_ENTITY = "nonsensical_entity"
SELF_CONTRADICTION = "self_contradiction"
CHECKS = (GRAMMAR, DUPLICATE, NONSENSICAL_ENTITY, SELF_CONTRADICTION)

# Nouns for relatives: a company or a place can't have them ("Alder Partners's nephew").
KIN_NOUNS = {
    "aunt",
    "brother",
    "cousin",
    "daughter",
    "father",
    "grandfather",
    "grandmother",
    "husband",
    "mother",
    "nephew",
    "niece",
    "parent",
    "sister",
    "son",
    "uncle",
    "wife",
}
BRACKETS = '()[]{}"“”'
SENTENCE_END = ".!?:;"
# Words between the two numbers of a range: "from 3 to 5", "between 3 and 5".
RANGE_RE = re.compile(r"\b(from|between)\s+\S*$", re.IGNORECASE)
RANGE_MIDDLE = {"from": re.compile(r"^\S*\s+(to|until|through)\s+\S*$", re.IGNORECASE)}
RANGE_MIDDLE["between"] = re.compile(r"^\S*\s+and\s+\S*$", re.IGNORECASE)
WORD_RE = re.compile(r"\w+")
# A sentence ends at . ! or ? (maybe followed by closing quotes or brackets) before a space.
SENTENCE_END_RE = re.compile("[.!?][\"'\u201d\u2019)\\]]*(?=\\s)|\n")


@dataclass(frozen=True)
class Problem:
    """One reason a mutant is broken: which check failed, why, and the mutant's sentence."""

    check: str
    explanation: str
    sentence: str

    def as_dict(self) -> dict[str, str]:
        return {"check": self.check, "explanation": self.explanation, "sentence": self.sentence}


@dataclass(frozen=True)
class _Swap:
    """One substitution, with where it sits in the original and in the mutant."""

    original: str
    replacement: str
    start: int  # in the original text
    end: int
    mutated_start: int
    mutated_end: int
    label: str | None  # entity swap's entity type
    kind: str | None  # numeric perturbation's kind of number


@lru_cache(maxsize=256)
def _parse(text: str):
    """spaCy's parse of an original answer. Cached, since every intensity mutates the same one."""
    return _nlp()(text)


def _swaps(substitutions: Sequence[Mapping[str, Any]], original_text: str) -> list[_Swap]:
    """The substitutions that say where they are, in order, mapped onto the mutated text."""
    swaps, shift = [], 0
    usable = [
        s
        for s in substitutions
        if isinstance(s.get("start"), int)
        and isinstance(s.get("end"), int)
        and isinstance(s.get("original"), str)
        and isinstance(s.get("replacement"), str)
        and original_text[s["start"] : s["end"]] == s["original"]
    ]
    for s in sorted(usable, key=lambda s: s["start"]):
        start = s["start"] + shift
        swaps.append(
            _Swap(
                s["original"],
                s["replacement"],
                s["start"],
                s["end"],
                start,
                start + len(s["replacement"]),
                s.get("label"),
                s.get("kind"),
            )
        )
        shift += len(s["replacement"]) - len(s["original"])
    return swaps


def _to_mutated(position: int, swaps: list[_Swap]) -> int:
    """Where a position of the original text (outside every swap) lands in the mutant."""
    return position + sum(len(s.replacement) - len(s.original) for s in swaps if s.end <= position)


def _sentences(
    original_text: str, swap: _Swap, swaps: list[_Swap], mutated_text: str
) -> tuple[str, str]:
    """The original sentence holding ``swap``, and the same sentence in the mutant.

    Sentences end at ``.``, ``!`` or ``?`` before a space, or at a line break. spaCy's own
    splits are less steady on short sentences: it cuts "60% voted yes and 40% voted no." in two.
    """
    ends = [m.end() for m in SENTENCE_END_RE.finditer(original_text)]
    start = max((e for e in ends if e <= swap.start), default=0)
    end = min((e for e in ends if e >= swap.end), default=len(original_text))
    mutated = mutated_text[_to_mutated(start, swaps) : _to_mutated(end, swaps)]
    return original_text[start:end].strip(), mutated.strip()


def _value(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _word_in(word: str, text: str) -> list[int]:
    """Where ``word`` starts in ``text`` as a whole word, ignoring case."""
    pattern = r"(?<!\w)" + re.escape(word.casefold()) + r"(?!\w)"
    return [m.start() for m in re.finditer(pattern, text.casefold())]


# -- grammar ---------------------------------------------------------------------------------


def _doubled_word(swap: _Swap, mutated_text: str) -> str | None:
    """A word repeated across the edge of the swap: "the the Red Coats"."""
    words = WORD_RE.findall(swap.replacement)
    if not words:
        return None
    before = WORD_RE.findall(mutated_text[: swap.mutated_start])[-1:]
    after = WORD_RE.findall(mutated_text[swap.mutated_end :])[:1]
    edge_before = mutated_text[: swap.mutated_start][-1:]
    edge_after = mutated_text[swap.mutated_end :][:1]
    if before and edge_before.isspace() and before[0].casefold() == words[0].casefold():
        return f"'{before[0]} {words[0]}': a word is doubled where '{swap.replacement}' went in"
    if after and edge_after.isspace() and after[0].casefold() == words[-1].casefold():
        return f"'{words[-1]} {after[0]}': a word is doubled where '{swap.replacement}' went in"
    return None


def _punctuation(swap: _Swap) -> str | None:
    """Brackets or quotes the replacement opens or closes, or spacing it adds."""
    for mark in BRACKETS:
        if swap.replacement.count(mark) != swap.original.count(mark):
            return f"'{swap.replacement}' changes the {mark!r} marks of '{swap.original}'"
    if swap.replacement != swap.replacement.strip():
        return f"'{swap.replacement}' starts or ends with a space"
    return None


def _number_agreement(doc, swap: _Swap) -> str | None:
    """A count that no longer agrees with its noun: "1 cat" -> "2 cat", "3 days" -> "1 days"."""
    if swap.kind != "integer":
        return None
    old, new = _value(swap.original), _value(swap.replacement)
    if old is None or new is None:
        return None
    token = doc.char_span(swap.start, swap.end, alignment_mode="expand")[0]
    noun = token.head
    if token.dep_ != "nummod" or noun.i <= token.i or noun.i - token.i > 2:
        return None
    if old == 1 and new != 1 and noun.tag_ == "NN":
        return f"'{swap.replacement} {noun.text}': the noun is still singular"
    if old != 1 and new == 1 and noun.tag_ == "NNS":
        return f"'{swap.replacement} {noun.text}': the noun is still plural"
    return None


def _quoted_title(swap: _Swap, original_text: str) -> str | None:
    """A name replaced inside a short quoted title, so only part of the title changed."""
    if swap.label is None:
        return None
    line_start = max(original_text.rfind("\n", 0, swap.start), 0)
    opening = max(original_text.rfind(q, line_start, swap.start) for q in '"“')
    if opening < 0:
        return None
    closing = [p for p in (original_text.find(q, swap.end) for q in '"”') if p >= 0]
    if not closing:
        return None
    title = original_text[opening + 1 : min(closing)]
    if '"' in title or "\n" in title:
        return None
    words = title.split()
    if title.strip() == swap.original or not 1 < len(words) <= 8 or not title[:1].isupper():
        return None
    return f"'{swap.original}' is only part of the title '{title.strip()}'"


# -- nonsensical entities --------------------------------------------------------------------


def _nonsensical(doc, swap: _Swap, original_text: str) -> str | None:
    """A name put where it can't fit. Each rule reads spaCy's parse of the original."""
    if swap.label not in ("PERSON", "ORG", "GPE"):
        return None
    span = doc.char_span(swap.start, swap.end, alignment_mode="expand")
    root = span.root
    after = doc[span.end] if span.end < len(doc) else None
    next_after = doc[span.end + 1] if span.end + 1 < len(doc) else None

    # "Worcestershire sauce" -> "James sauce": the name was describing the noun after it.
    if (
        swap.label in ("PERSON", "ORG")
        and root.dep_ == "compound"
        and after is not None
        and root.head == after
        and after.pos_ == "NOUN"
        and after.text.islower()
    ):
        return f"'{swap.replacement}' put where '{swap.original}' describes '{after.text}'"

    # "Pat the salmon dry" -> "Thomas the salmon dry": a verb read as a person's name.
    starts_sentence = original_text[: swap.start].rstrip()[-1:] in ("", *SENTENCE_END)
    if (
        swap.label == "PERSON"
        and starts_sentence
        and after is not None
        and after.pos_ == "DET"
        and next_after is not None
        and next_after.text[:1].islower()
    ):
        return (
            f"'{swap.original}' starts an instruction as its verb, "
            f"so '{swap.replacement} {after.text} {next_after.text}' reads as nonsense"
        )

    if swap.label in ("ORG", "GPE"):
        # "Bilbo's nephew" -> "Alder Partners's nephew".
        if root.dep_ == "poss" and root.head.lemma_.lower() in KIN_NOUNS:
            return f"'{swap.replacement}' can't have a {root.head.text}"
        # "Smaug the dragon" -> "Rome the dragon".
        if (
            after is not None
            and after.lower_ == "the"
            and next_after is not None
            and next_after.pos_ == "NOUN"
            and next_after.dep_ == "appos"
            and next_after.text.islower()
        ):
            kind = "place" if swap.label == "GPE" else "company"
            return (
                f"'{swap.replacement} the {next_after.text}': a {kind} can't be a {next_after.text}"
            )
    return None


# -- duplicates and contradictions -----------------------------------------------------------


def _duplicate(swap: _Swap, mutated_sentence: str) -> str | None:
    """The new name or number appears more than once in the mutated sentence.

    Counted in the mutated sentence, not the original, so two swaps that trade values
    ("from Paris to London" -> "from London to Paris") aren't mistaken for a duplicate.
    """
    if swap.kind is not None:
        new = _value(swap.replacement)
        if new is not None and [n.value for n in find_numbers(mutated_sentence)].count(new) >= 2:
            return f"{swap.replacement} now appears twice in the sentence"
        return None
    if swap.label is not None and len(_word_in(swap.replacement, mutated_sentence)) >= 2:
        return f"'{swap.replacement}' now appears twice in the sentence"
    return None


def _ranges(sentence: str) -> list[tuple[Decimal, Decimal]]:
    """Every "from A to B" and "between A and B" in a sentence, in order."""
    numbers = find_numbers(sentence)
    ranges = []
    for first, second in pairwise(numbers):
        opening = RANGE_RE.search(sentence[: first.start])
        if opening is None or sentence[opening.end() : first.start].strip():
            continue
        middle = RANGE_MIDDLE[opening.group(1).lower()]
        if middle.match(sentence[first.end : second.start]):
            ranges.append((first.value, second.value))
    return ranges


def _percent_total(sentence: str) -> Decimal | None:
    percents = [n.value for n in find_numbers(sentence) if n.kind == "percent"]
    return sum(percents, Decimal(0)) if len(percents) > 1 else None


def _contradiction(swap: _Swap, sentence: str, mutated_sentence: str) -> str | None:
    """A sentence that now contradicts itself: a reversed range, or shares over 100%."""
    if swap.kind is None:
        return None
    before, after = _ranges(sentence), _ranges(mutated_sentence)
    if len(before) == len(after):
        for (a, b), (c, d) in zip(before, after, strict=True):
            if a < b and c >= d:
                return f"the range now runs from {c} to {d}, backwards"
    old_total, new_total = _percent_total(sentence), _percent_total(mutated_sentence)
    if old_total == 100 and new_total is not None and new_total > 100:
        return f"percentages that added up to 100% now add up to {new_total}%"
    return None


# -- entry points ----------------------------------------------------------------------------


def check_text(
    original_text: str, mutated_text: str, substitutions: Sequence[Mapping[str, Any]]
) -> list[Problem]:
    """Every problem the substitutions introduced, or an empty list when the mutant is valid.

    ``substitutions`` is the operator's ``details["substitutions"]``: each one needs
    ``original``, ``replacement``, ``start`` and ``end`` (in the original text), and
    ``label`` (entity swap) or ``kind`` (numeric perturbation) for the checks that need them.
    """
    swaps = _swaps(substitutions, original_text)
    if not swaps:
        return []
    doc = _parse(original_text)
    problems = []
    for swap in swaps:
        sentence, mutated_sentence = _sentences(original_text, swap, swaps, mutated_text)
        found = [
            (GRAMMAR, _doubled_word(swap, mutated_text)),
            (GRAMMAR, _punctuation(swap)),
            (GRAMMAR, _number_agreement(doc, swap)),
            (GRAMMAR, _quoted_title(swap, original_text)),
            (DUPLICATE, _duplicate(swap, mutated_sentence)),
            (NONSENSICAL_ENTITY, _nonsensical(doc, swap, original_text)),
            (SELF_CONTRADICTION, _contradiction(swap, sentence, mutated_sentence)),
        ]
        problems += [Problem(check, why, mutated_sentence) for check, why in found if why]
    return problems


def check_mutant(mutant: Mutant) -> list[Problem]:
    """Why ``mutant`` is broken, or an empty list when it's valid or isn't degrading.

    Preserving operators are never checked: reformatting into bullet points breaks sentences
    on purpose.
    """
    if mutant.operator_family is not OperatorFamily.DEGRADING or not mutant.changed:
        return []
    substitutions = mutant.details.get("substitutions")
    if not isinstance(substitutions, (list, tuple)):
        return []
    return check_text(mutant.original_text, mutant.mutated_text, substitutions)
