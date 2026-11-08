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


# A list item: "-", "*", "+" or "•" bullets, or "1." / "1)" numbering, then a space.
_LIST_ITEM = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")
_HEADING = re.compile(r"^#{1,6}\s+")
# The next number of a numbered list written on one line, after a sentence ends.
_INLINE_NUMBER = re.compile(r"(?<=[.!?])\s+\d+[.)]\s+")
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
    """Remove inline markup (bold, escapes) and collapse whitespace."""
    text = text.replace("**", "")
    text = _ESCAPE.sub(r"\1", text)
    return " ".join(text.split())


def detect_format(text: str) -> Format:
    """Which format ``text`` is in: markdown if it has bold or headings, bullets if a list."""
    lines = [line.strip() for line in text.splitlines()]
    if "**" in text or any(_HEADING.match(line) for line in lines):
        return Format.MARKDOWN
    if any(_LIST_ITEM.match(line) for line in lines):
        return Format.BULLETS
    return Format.PROSE


def split_units(text: str) -> list[str]:
    """Split ``text`` into the units a layout is built from.

    List items and headings are one unit each, whatever they contain. Prose between them is
    split into sentences. Markup is removed from every unit, and its words are kept as they are.
    """
    units: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            doc = _nlp()(" ".join(paragraph))
            units.extend(_clean(sent.text) for sent in doc.sents)
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.strip()
        marker = _LIST_ITEM.match(line) or _HEADING.match(line)
        if not line:
            flush()
        elif marker:
            flush()
            # "1. Do this. 2. Do that." on one line is a list too: split it at each number.
            items = [line[marker.end() :]]
            if line[0].isdigit():
                items = _INLINE_NUMBER.split(items[0])
            units.extend(_clean(item) for item in items)
        else:
            paragraph.append(line)
    flush()
    return [unit for unit in units if _words(unit)]


def _escape_inline(text: str) -> str:
    """Escape asterisks, so text never turns into emphasis by accident."""
    return text.replace("\\", "\\\\").replace("*", "\\*")


def _escape_start(text: str) -> str:
    """Escape what would make a list item read as a nested list, heading, or quote."""
    numbered = re.match(r"^(\d+)([.)])(\s|$)", text)
    if numbered:
        return f"{numbered[1]}\\{numbered[2]}{text[numbered.end(2) :]}"
    if re.match(r"^[-+#>]", text):
        return "\\" + text
    return text


def _bold_lead(unit: str) -> str:
    """Escape ``unit`` and bold its first noun phrase, if one starts in its first few words."""
    doc = _nlp()(unit)
    chunk = next(iter(doc.noun_chunks), None)
    if chunk is None or chunk.start > 2 or chunk.end == len(doc):
        return _escape_start(_escape_inline(unit))
    start, end = chunk.start_char, chunk.end_char
    before = _escape_inline(unit[:start])
    lead = _escape_inline(unit[start:end])
    after = _escape_inline(unit[end:])
    return f"{_escape_start(before) if before else ''}**{lead}**{after}"


def render(units: list[str], target: Format) -> str:
    """Lay ``units`` out in ``target`` format."""
    if target is Format.PROSE:
        sentences = [u if u.endswith(_CLOSING) else f"{u}." for u in units]
        return " ".join(sentences)
    if target is Format.BULLETS:
        return "\n".join(f"- {_escape_start(_escape_inline(u))}" for u in units)
    return "\n".join(f"{i}. {_bold_lead(u)}" for i, u in enumerate(units, start=1))


class ClaimChangedError(RuntimeError):
    """A conversion would have changed the answer's words, so it was refused."""


def reformat(text: str, target: Format) -> str:
    """Return ``text`` laid out in ``target`` format, with exactly the same words.

    Raises:
        ClaimChangedError: the output's words differ from the input's. This is a bug in the
            conversion, and returning the text could change a claim.
    """
    units = split_units(text)
    if not units:
        return text
    output = render(units, Format(target))
    # Read the output back as an answer: numbering and markup drop out, and the words must
    # come back exactly as they went in.
    if _words(" ".join(split_units(output))) != _words(" ".join(units)):
        raise ClaimChangedError(f"converting to {target} changed the answer's words")
    return output


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
        elif source is self.target:
            details["reason"] = "already_in_target_format"
        else:
            mutated = reformat(text, self.target)
            details["units"] = len(split_units(text))
            logger.debug(
                "%s: %d units, %s -> %s", self.metadata.name, details["units"], source, self.target
            )
            return MutationResult(
                original_text=text,
                mutated_text=mutated,
                changed=mutated != text,
                details=details,
            )
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
