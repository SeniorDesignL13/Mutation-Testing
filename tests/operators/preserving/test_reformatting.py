"""Tests for the reformatting operators: same words, different layout."""

import inspect
import re

import pytest

from mtlj.operators.base import ExpectedDirection, OperatorFamily
from mtlj.operators.preserving import reformatting
from mtlj.operators.preserving.reformatting import (
    ClaimChangedError,
    Format,
    ReformattingOperator,
    ReformatToBulletsOperator,
    ReformatToMarkdownOperator,
    ReformatToProseOperator,
    detect_format,
    reformat,
    split_units,
)

PROSE = (
    "The Sydney Harbour Bridge opened in 1932. It cost about 6.25 million pounds. "
    "Dr. Bradfield oversaw the design, and the arch spans 503 metres!"
)
BULLETS = (
    "Ways to save water include:\n"
    "- Take shorter showers\n"
    "* Fix leaking taps within 2 days.\n"
    "• Water the lawn early (before 8am)."
)
NUMBERED_ONE_LINE = "1. Remove the storm window. 2. Pry off the siding. 3. Trim the flashing."
MARKDOWN = "1. **The bridge** opened in 1932.\n2. **It** cost 6.25 million pounds."

ANSWERS = [PROSE, BULLETS, NUMBERED_ONE_LINE, MARKDOWN]
OPERATORS = [ReformatToProseOperator, ReformatToBulletsOperator, ReformatToMarkdownOperator]


def words(text: str) -> list[str]:
    """Words and numbers in order, ignoring list numbering, markup and punctuation."""
    return re.findall(r"[^\W_]+", " ".join(split_units(text)))


def assert_same_claims(before: str, after: str) -> None:
    """Every word and number survives, in order: nothing a claim is made of has changed.

    Sentence boundaries can move ("include: Take shorter showers." is one sentence in prose but
    two list items), so only the words are compared.
    """
    assert words(after) == words(before)


def assert_valid_bullets(text: str) -> None:
    for line in text.splitlines():
        assert re.match(r"^- \S", line), line
        assert not re.match(r"^- (?:[-+*#>]|\d+[.)](?:\s|$))", line), f"nested markup: {line}"
        assert "**" not in line


def assert_valid_markdown(text: str) -> None:
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        assert line.startswith(f"{number}. "), line
        unescaped_stars = re.findall(r"(?<!\\)\*\*", line)
        assert len(unescaped_stars) % 2 == 0, f"unbalanced bold: {line}"
        for bold in re.findall(r"(?<!\\)\*\*(.+?)(?<!\\)\*\*", line):
            assert bold == bold.strip(), f"bold with space inside its edges won't render: {line}"


def assert_valid_prose(text: str) -> None:
    assert "\n" not in text
    assert not re.search(r"(?m)^\s*(?:[-*+•]|\d+[.)])\s", text)
    assert "**" not in text


VALID = {
    Format.PROSE: assert_valid_prose,
    Format.BULLETS: assert_valid_bullets,
    Format.MARKDOWN: assert_valid_markdown,
}


@pytest.mark.parametrize("operator", OPERATORS)
def test_metadata_is_preserving_unchanged(operator) -> None:
    meta = operator.metadata
    assert meta.family is OperatorFamily.PRESERVING
    assert meta.expected_direction is ExpectedDirection.UNCHANGED
    assert meta.name == f"reformat_to_{operator.target.value}"


def test_base_class_is_abstract_so_discovery_skips_it() -> None:
    assert inspect.isabstract(ReformattingOperator)
    assert not any(inspect.isabstract(op) for op in OPERATORS)


@pytest.mark.parametrize("answer", ANSWERS)
@pytest.mark.parametrize("target", list(Format))
def test_no_claim_is_added_removed_or_altered(answer: str, target: Format) -> None:
    output = reformat(answer, target)
    assert_same_claims(answer, output)
    VALID[target](output)


@pytest.mark.parametrize("answer", ANSWERS)
@pytest.mark.parametrize("first", list(Format))
@pytest.mark.parametrize("second", list(Format))
def test_converts_in_both_directions(answer: str, first: Format, second: Format) -> None:
    there = reformat(answer, first)
    back = reformat(there, second)
    assert_same_claims(answer, back)
    VALID[second](back)


def test_prose_to_bullets_puts_one_sentence_per_bullet() -> None:
    assert reformat(PROSE, Format.BULLETS) == (
        "- The Sydney Harbour Bridge opened in 1932.\n"
        "- It cost about 6.25 million pounds.\n"
        "- Dr. Bradfield oversaw the design, and the arch spans 503 metres!"
    )


def test_bullets_to_prose_ends_every_item_as_a_sentence() -> None:
    assert reformat(BULLETS, Format.PROSE) == (
        "Ways to save water include: Take shorter showers. Fix leaking taps within 2 days. "
        "Water the lawn early (before 8am)."
    )


def test_markdown_numbers_items_and_bolds_a_lead_phrase() -> None:
    output = reformat(PROSE, Format.MARKDOWN)
    assert output.startswith("1. **The Sydney Harbour Bridge** opened in 1932.\n2. ")
    assert_valid_markdown(output)


def test_a_numbered_list_on_one_line_is_split_into_items() -> None:
    assert split_units(NUMBERED_ONE_LINE) == [
        "Remove the storm window.",
        "Pry off the siding.",
        "Trim the flashing.",
    ]


def test_text_that_looks_like_markup_is_escaped_and_survives() -> None:
    answer = "The score was 5*3 points. 2. Is not a list item here, it follows a sentence."
    for target in Format:
        output = reformat(answer, target)
        assert_same_claims(answer, output)
        VALID[target](output)
    assert "5\\*3" in reformat(answer, Format.BULLETS)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(PROSE, Format.PROSE), (BULLETS, Format.BULLETS), (MARKDOWN, Format.MARKDOWN)],
)
def test_detect_format(answer: str, expected: Format) -> None:
    assert detect_format(answer) is expected


@pytest.mark.parametrize("operator", OPERATORS)
def test_answer_already_in_target_format_is_unchanged(operator) -> None:
    answer = {Format.PROSE: PROSE, Format.BULLETS: BULLETS, Format.MARKDOWN: MARKDOWN}[
        operator.target
    ]
    result = operator().apply(answer, [])
    assert not result.changed
    assert result.mutated_text == answer
    assert result.details["reason"] == "already_in_target_format"


def test_zero_intensity_changes_nothing() -> None:
    result = ReformatToBulletsOperator().apply(PROSE, [], intensity=0.0)
    assert not result.changed
    assert result.mutated_text == PROSE
    assert result.details["reason"] == "zero_intensity"


@pytest.mark.parametrize("intensity", [0.1, 0.5, 1.0])
def test_any_positive_intensity_converts_the_whole_answer(intensity: float) -> None:
    result = ReformatToBulletsOperator().apply(PROSE, [], intensity=intensity)
    assert result.changed
    assert result.mutated_text == reformat(PROSE, Format.BULLETS)
    assert result.details == {"source_format": "prose", "target_format": "bullets", "units": 3}


def test_same_input_gives_the_same_output() -> None:
    op = ReformatToMarkdownOperator()
    assert op.apply(PROSE, [], seed=1).mutated_text == op.apply(PROSE, [], seed=2).mutated_text


def test_a_conversion_that_would_change_words_is_refused(monkeypatch) -> None:
    def drop_last_unit(units: list[str], target: Format) -> str:
        return " ".join(units[:-1])

    monkeypatch.setattr(reformatting, "render", drop_last_unit)
    with pytest.raises(ClaimChangedError):
        reformat(PROSE, Format.BULLETS)
