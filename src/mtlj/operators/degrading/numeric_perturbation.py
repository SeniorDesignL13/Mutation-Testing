"""Numeric perturbation operator: changes a number the source states, so the answer is wrong.

The answer is mutated so that one number in it disagrees with its context. The rules:

* Only numbers that also appear in the context are targeted (compared by value, so ``1,200``
  matches ``1200``), so the claim genuinely becomes false.
* Exactly one number is changed. Numbers that appear once in the answer are preferred, so a
  repeated value is never changed everywhere.
* Formatting is kept: the same number of decimal places, thousands separators if the original
  had them, and everything around the digits (``%``, ``$``, units) untouched.
* The new value never matches any number in the context, so no passage can support it.
* The new value is above zero, and a percentage of at most 100 stays at most 100.
* A four-digit whole number from 1000 to 2099 without a thousands separator is read as a year.

Numbers are found with a regular expression, and only those standing on their own: digits
joined to letters or to other numbers (``COVID-19``, ``3rd``, ``10:30``, ``3/4``, ``v2.0.1``,
``2019-03-04``) are left alone. Numbers written as words ("three") aren't changed.

Intensity sets how big the change is. Years and days of the month (``March 5``, ``5 March``)
move by ``ceil(intensity * 10)``, other numbers by ``intensity * 50%`` of their value, rounded
to their own precision and at least one unit of their last digit. Intensity 0 changes nothing.
Every change is recorded in ``details["substitutions"]`` (the provenance).
"""

import math
import random
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
    ParameterSpec,
)

MAX_RELATIVE_CHANGE = 0.5
MAX_SHIFT = 10
# How far past the requested change to look for a value the context doesn't hold.
MAX_EXTRA_STEPS = 25

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# Thousands groups first, so "1,200" is one number rather than "1" and "200".
NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
PERCENT_AFTER_RE = re.compile(r"\s?(?:%|percent\b|per cent\b)", re.IGNORECASE)
MONTH_BEFORE_RE = re.compile(r"\b(?:" + MONTHS + r")\s+$")
MONTH_AFTER_RE = re.compile(r"^\s+(?:of\s+)?(?:" + MONTHS + r")\b")
# Characters that join a number to another number: 1.2.3, 10:30, 3/4, 2019-03, 1,2,3.
JOINERS = ".,:/-"


@dataclass
class _Number:
    text: str
    start: int
    end: int
    value: Decimal
    kind: str  # "year", "day", "percent", "decimal" or "integer"


def _standalone(text: str, start: int, end: int) -> bool:
    """True when the digits at text[start:end] are a number of their own."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before.isalnum() or before == "_" or after.isalnum() or after == "_":
        return False
    if before in JOINERS and start >= 2 and text[start - 2].isalnum():
        return False
    return not (after in JOINERS and end + 1 < len(text) and text[end + 1].isdigit())


def _kind(text: str, start: int, end: int, raw: str, value: Decimal) -> str:
    if PERCENT_AFTER_RE.match(text, end):
        return "percent"
    if "." in raw:
        return "decimal"
    if "," not in raw and len(raw) == 4 and 1000 <= value <= 2099:
        return "year"
    if 1 <= value <= 31 and (
        MONTH_BEFORE_RE.search(text[:start]) or MONTH_AFTER_RE.match(text[end:])
    ):
        return "day"
    return "integer"


def find_numbers(text: str) -> list[_Number]:
    """Every standalone number in ``text``, in order."""
    numbers = []
    for match in NUMBER_RE.finditer(text):
        start, end = match.span()
        if not _standalone(text, start, end):
            continue
        raw = match.group()
        value = Decimal(raw.replace(",", ""))
        numbers.append(_Number(raw, start, end, value, _kind(text, start, end, raw, value)))
    return numbers


def _format(value: Decimal, original: str) -> str:
    """Write ``value`` the way ``original`` was written."""
    places = len(original.split(".")[1]) if "." in original else 0
    grouping = "," if "," in original else ""
    return f"{value:{grouping}.{places}f}"


class NumericPerturbationOperator(MutationOperator):
    """Changes a number, date or quantity the context states, so the answer contradicts it.

    This makes the text contradict its source, so a good judge's score should decrease.
    """

    metadata = OperatorMetadata(
        name="numeric_perturbation",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description=(
            "Changes one number (integer, decimal, percentage, year or day of the month) that "
            "the context states to a value the context doesn't, keeping its format and units. "
            "intensity controls how far the number moves."
        ),
        version=1,
        parameters=[
            ParameterSpec(
                name="relative_change",
                description=(
                    "Change to a number as a fraction of its value, reached at intensity 1.0."
                ),
                default=MAX_RELATIVE_CHANGE,
                min_value=0.0,
                max_value=MAX_RELATIVE_CHANGE,
            ),
            ParameterSpec(
                name="date_shift",
                description="Years or days a date moves by, reached at intensity 1.0.",
                default=MAX_SHIFT,
                min_value=1,
                max_value=MAX_SHIFT,
            ),
        ],
    )

    @staticmethod
    def _candidates(number: _Number, intensity: float, sign: int) -> list[Decimal]:
        """Values to try for ``number`` in one direction, the requested change first."""
        if number.kind in ("year", "day"):
            shift = math.ceil(intensity * MAX_SHIFT)
            return [number.value + sign * (shift + extra) for extra in range(MAX_EXTRA_STEPS)]
        places = len(number.text.split(".")[1]) if "." in number.text else 0
        unit = Decimal(1).scaleb(-places)
        relative = Decimal(str(intensity * MAX_RELATIVE_CHANGE))
        target = (number.value * (1 + sign * relative)).quantize(unit, rounding=ROUND_HALF_UP)
        if target == number.value:
            target += sign * unit  # rounding undid the change: move one unit of the last digit
        return [target + sign * extra * unit for extra in range(MAX_EXTRA_STEPS)]

    @staticmethod
    def _allowed(number: _Number, value: Decimal, context_values: set[Decimal]) -> bool:
        if value == number.value or value in context_values:
            return False
        if value <= 0:  # the digits never carry a sign, so a count stays a count
            return False
        if number.kind == "year":
            return 1000 <= value <= 2099
        if number.kind == "day":
            return 1 <= value <= 28  # a day every month has
        return not (number.kind == "percent" and number.value <= 100 < value)

    def _replacement(
        self, number: _Number, intensity: float, context_values: set[Decimal], rng: random.Random
    ) -> Decimal | None:
        signs = [1, -1]
        rng.shuffle(signs)
        for sign in signs:
            for value in self._candidates(number, intensity, sign):
                if self._allowed(number, value, context_values):
                    return value
        return None

    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Change one context-supported number in ``text`` by an amount set by ``intensity``."""
        rng = random.Random(seed)
        intensity = min(max(intensity, 0.0), 1.0)
        context_values = {n.value for passage in context for n in find_numbers(passage)}
        skipped = {"no_context_support": 0, "no_valid_replacement": 0}

        numbers = find_numbers(text)
        candidates = []
        for number in numbers:
            if number.value in context_values:
                candidates.append(number)
            else:
                skipped["no_context_support"] += 1
        occurrences = {n.value: sum(1 for o in numbers if o.value == n.value) for n in candidates}
        rng.shuffle(candidates)
        candidates.sort(key=lambda n: occurrences[n.value] > 1)  # once-only first

        chosen = None
        if intensity > 0:
            for number in candidates:
                new_value = self._replacement(number, intensity, context_values, rng)
                if new_value is None:
                    skipped["no_valid_replacement"] += 1
                    continue
                chosen = (number, new_value)
                break

        substitutions = []
        mutated = text
        if chosen:
            number, new_value = chosen
            replacement = _format(new_value, number.text)
            mutated = text[: number.start] + replacement + text[number.end :]
            change = new_value - number.value
            substitutions.append(
                {
                    "original": number.text,
                    "replacement": replacement,
                    "kind": number.kind,
                    "start": number.start,
                    "end": number.end,
                    "change": float(change),
                    "relative_change": float(change / number.value) if number.value else None,
                    "occurrences_in_answer": occurrences[number.value],
                }
            )
        return MutationResult(
            original_text=text,
            mutated_text=mutated,
            changed=mutated != text,
            details={
                "substitutions": substitutions,
                "candidates": len(candidates),
                "skipped": skipped,
            },
        )
