"""Reformatting operators: present the same answer as prose, a bullet list, or markdown.

The answer is split into units (sentences of prose, or the items of an existing list), and the
units are laid out in the target format. No word is added, dropped, or changed: only layout and
markup (bullet markers, numbering, bold) are. A judge whose score moves is reacting to
presentation, not to faithfulness, so a good judge's score should stay the same.

Formats:

* ``prose``: the units joined into one paragraph. A unit with no closing punctuation (a list
  item like ``- Hydrogen peroxide``) gets a period, so sentences don't run together.
* ``bullets``: one ``- `` bullet per unit, no other markup.
* ``markdown``: a numbered list, each item's first noun phrase in **bold**.

``reformat(text, target)`` takes the target as a parameter. The registered operators fix it
(``reformat_to_prose``, ``reformat_to_bullets``, ``reformat_to_markdown``), because a mutant is
rebuilt from its operator's name and only numeric parameters are saved with it.

Reformatting has no natural dose, so intensity is all or nothing: any intensity above 0
converts the whole answer, and 0 changes nothing. An answer already in the target format is
returned unchanged.

Every conversion is checked: the words of the output (markup removed) must equal the words of
the input, in order. A conversion that fails the check raises instead of returning a mutant
that could carry a different claim.
"""

import logging
import re
from abc import abstractmethod
from enum import StrEnum
from functools import lru_cache

import spacy

from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)

logger = logging.getLogger(__name__)


class Format(StrEnum):
    """The layouts an answer can be converted between."""

    PROSE = "prose"
    BULLETS = "bullets"
    MARKDOWN = "markdown"


# A bulleted item: "-", "*", "+" or "•", then a space.
_BULLET = re.compile(r"^[-*+•]\s+")
# A numbered item: "1." or "1)", then a space. Whether it really is one depends on its number
# (see ``_parse``), so a wrapped line like "1932. It cost..." stays prose.
_NUMBERED = re.compile(r"^(\d{1,3})[.)]\s+")
_HEADING = re.compile(r"^#{1,6}\s+")
# A possible next number of a numbered list written on one line, after a sentence ends.
_INLINE_NUMBER = re.compile(r"(?<=[.!?])\s+(\d{1,3})[.)]\s+")
# Bold: a pair of "**" around text that doesn't start or end with a space. A lone "**" (as in
# "2**3") isn't bold and is kept.
_BOLD = re.compile(r"(?<!\\)\*\*(?=\S)(.+?)(?<=\S)(?<!\\)\*\*")
# A backslash escape that this module (or any markdown writer) puts before punctuation.
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|])")
# Closing punctuation: a unit ending in one of these already ends as a sentence. The curly
# quotes are meant, not look-alikes.
_CLOSING = (".", "!", "?", ":", ";", "…", '"', "'", "”", "’", ")")  # noqa: RUF001


@lru_cache
def _nlp():
    """Load the spaCy pipeline once and cache it. Only sentences and noun chunks are used."""
    return spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer"])


def _words(text: str) -> list[str]:
    """The words and numbers in ``text``, in order. Markup and punctuation don't count."""
    return re.findall(r"[^\W_]+", text)


def _clean(text: str) -> str:
    """Remove inline markup (bold pairs, escapes) and collapse whitespace."""
    text = _BOLD.sub(r"\1", text)
    text = _ESCAPE.sub(r"\1", text)
    return " ".join(text.split())


def detect_format(text: str) -> Format:
    """Which format ``text`` is in.

    Markdown if it has headings, bold, or a numbered list; bullets if it has only bulleted
    items; prose otherwise.
    """
    _, numbers = _parse(text)
    lines = [line.strip() for line in text.splitlines()]
    if numbers or _BOLD.search(text) or any(_HEADING.match(line) for line in lines):
        return Format.MARKDOWN
    if any(_BULLET.match(line) for line in lines):
        return Format.BULLETS
    return Format.PROSE


def _parse(text: str) -> tuple[list[str], list[str]]:
    """Split ``text`` into units, and return the list numbers its markup used.

    List items and headings are one unit each, whatever they contain. Prose between them is
    split into sentences. Markup is removed from every unit, and its words are kept as they are.

    A numbered line is a list item only if its number is 1 or follows the item before (the way
    markdown decides whether a number can start a list), so "1932. It cost a lot." on a
    wrapped line stays a sentence. The numbers that were list markup are returned, so the
    caller can check that no other word went missing.
    """
    units: list[str] = []
    numbers: list[str] = []
    paragraph: list[str] = []
    last_number = 0

    def flush() -> None:
        if paragraph:
            doc = _nlp()(" ".join(paragraph))
            units.extend(_clean(sent.text) for sent in doc.sents)
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.strip()
        numbered = _NUMBERED.match(line)
        if numbered and int(numbered[1]) not in (1, last_number + 1):
            numbered = None
        marker = numbered or _BULLET.match(line) or _HEADING.match(line)
        if not line:
            flush()
        elif marker:
            flush()
            item = line[marker.end() :]
            if numbered:
                last_number = int(numbered[1])
                numbers.append(numbered[1])
                # "1. Do this. 2. Do that." on one line is a list too: split it at each next
                # number, but only the next one, so "mix. 7. Then" inside an item stays put.
                start = 0
                for match in _INLINE_NUMBER.finditer(item):
                    if int(match[1]) == last_number + 1:
                        units.append(_clean(item[start : match.start()]))
                        last_number += 1
                        numbers.append(match[1])
                        start = match.end()
                item = item[start:]
            units.append(_clean(item))
        else:
            last_number = 0
            paragraph.append(line)
    flush()
    return [unit for unit in units if _words(unit)], numbers


def split_units(text: str) -> list[str]:
    """Split ``text`` into the units a layout is built from (see ``_parse``)."""
    return _parse(text)[0]


def _words_kept(text: str, units: list[str], numbers: list[str]) -> bool:
    """Whether ``units`` hold every word of ``text``, in order, apart from its list numbers.

    This reads ``text`` itself, not a parse of it, so a word the parser wrongly took for markup
    (or two words it wrongly joined) is caught.
    """
    expected = iter(_words(" ".join(units)))
    numbers_left = list(numbers)
    want = next(expected, None)
    for word in _words(text):
        if word == want:
            want = next(expected, None)
        elif word in numbers_left:
            numbers_left.remove(word)
        else:
            return False
    return want is None


def _escape_inline(text: str) -> str:
    """Escape asterisks, so text never turns into emphasis by accident."""
    return text.replace("\\", "\\\\").replace("*", "\\*")


def _escape_numbers(text: str) -> str:
    """Escape "2." after a sentence inside a list item, so it can't read as the next item."""
    return _INLINE_NUMBER.sub(lambda m: m[0].replace(f"{m[1]}", f"{m[1]}\\", 1), text)


def _escape_start(text: str) -> str:
    """Escape what would make a line read as a list item, heading, or quote."""
    numbered = re.match(r"^(\d+)([.)])(\s|$)", text)
    if numbered:
        return f"{numbered[1]}\\{numbered[2]}{text[numbered.end(2) :]}"
    if re.match(r"^[-+#>]", text):
        return "\\" + text
    return text


def _flanks(before: str, lead: str, after: str) -> bool:
    """Whether ``**lead**`` renders as bold between ``before`` and ``after`` (CommonMark).

    Bold that starts or ends with punctuation needs a space (or the line's edge) outside it:
    "**U.S.**x" shows its asterisks.
    """
    opens = lead[0].isalnum() or not before or before[-1].isspace()
    closes = lead[-1].isalnum() or not after or after[0].isspace()
    return opens and closes


def _bold_lead(unit: str, doc) -> str:
    """Escape ``unit`` and bold its first noun phrase, if one starts in its first few words."""
    chunk = next(iter(doc.noun_chunks), None)
    if chunk is not None and chunk.start <= 2 and chunk.end < len(doc):
        before, lead, after = (
            unit[: chunk.start_char],
            unit[chunk.start_char : chunk.end_char],
            unit[chunk.end_char :],
        )
        if _flanks(before, lead, after):
            before = _escape_start(_escape_inline(before)) if before else ""
            return f"{before}**{_escape_inline(lead)}**{_escape_numbers(_escape_inline(after))}"
    return _escape_start(_escape_numbers(_escape_inline(unit)))


def render(units: list[str], target: Format) -> str:
    """Lay ``units`` out in ``target`` format."""
    if target is Format.PROSE:
        sentences = [u if u.endswith(_CLOSING) else f"{u}." for u in units]
        return _escape_start(" ".join(sentences))
    if target is Format.BULLETS:
        items = (_escape_start(_escape_numbers(_escape_inline(u))) for u in units)
        return "\n".join(f"- {item}" for item in items)
    docs = _nlp().pipe(units)
    items = (_bold_lead(u, doc) for u, doc in zip(units, docs, strict=True))
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


class ClaimChangedError(RuntimeError):
    """A conversion would have changed the answer's words, so it was refused."""


def _convert(text: str, target: Format) -> tuple[str, int]:
    """``reformat``, plus the number of units the answer was split into."""
    units, numbers = _parse(text)
    if not units:
        return text, 0
    # Check the parse against the text itself: only list numbers may be missing.
    if not _words_kept(text, units, numbers):
        raise ClaimChangedError("splitting the answer into sentences changed its words")
    output = render(units, Format(target))
    # Read the output back as an answer: the same words must come back, in the same order.
    out_units, out_numbers = _parse(output)
    if _words(" ".join(out_units)) != _words(" ".join(units)) or not _words_kept(
        output, out_units, out_numbers
    ):
        raise ClaimChangedError(f"converting to {target} changed the answer's words")
    return output, len(units)


def reformat(text: str, target: Format) -> str:
    """Return ``text`` laid out in ``target`` format, with exactly the same words.

    Raises:
        ClaimChangedError: the output's words differ from the input's. This is a bug in the
            conversion, and returning the text could change a claim.
    """
    return _convert(text, target)[0]


class ReformattingOperator(MutationOperator):
    """Converts an answer to another format without changing a word of it.

    Subclasses set ``target``; registry discovery skips this base class because it's abstract.
    """

    @property
    @abstractmethod
    def target(self) -> Format:
        """The format this operator converts answers to."""

    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Convert ``text`` to ``target`` format. ``context`` and ``seed`` aren't used."""
        source = detect_format(text)
        details: dict = {"source_format": source.value, "target_format": self.target.value}

        if intensity <= 0:
            details["reason"] = "zero_intensity"
        # Prose is the one target where converting prose would only re-space it.
        elif source is Format.PROSE and self.target is Format.PROSE:
            details["reason"] = "already_in_target_format"
        else:
            mutated, details["units"] = _convert(text, self.target)
            if mutated != text:
                logger.debug(
                    "%s: %d units, %s -> %s",
                    self.metadata.name,
                    details["units"],
                    source,
                    self.target,
                )
                return MutationResult(
                    original_text=text, mutated_text=mutated, changed=True, details=details
                )
            details["reason"] = "already_in_target_format"
        return MutationResult(original_text=text, mutated_text=text, changed=False, details=details)


def _metadata(target: Format, layout: str) -> OperatorMetadata:
    return OperatorMetadata(
        name=f"reformat_to_{target.value}",
        family=OperatorFamily.PRESERVING,
        expected_direction=ExpectedDirection.UNCHANGED,
        description=(
            f"Lays the answer out as {layout}, keeping every word as it is, so no claim is "
            "added, removed or altered. All or nothing: any intensity above 0 converts the "
            "whole answer."
        ),
    )


class ReformatToProseOperator(ReformattingOperator):
    """Joins a list (bulleted, numbered or markdown) into one paragraph of prose."""

    target = Format.PROSE
    metadata = _metadata(Format.PROSE, "one paragraph of prose")


class ReformatToBulletsOperator(ReformattingOperator):
    """Puts each sentence or list item on its own plain ``- `` bullet."""

    target = Format.BULLETS
    metadata = _metadata(Format.BULLETS, "a plain bullet list, one sentence per bullet")


class ReformatToMarkdownOperator(ReformattingOperator):
    """Makes a numbered markdown list, each item's first noun phrase in bold."""

    target = Format.MARKDOWN
    metadata = _metadata(Format.MARKDOWN, "a numbered markdown list with bold lead phrases")
