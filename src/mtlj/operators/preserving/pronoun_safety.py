"""Which sentences of an answer can be reordered without changing what it says (MTLJ-80).

Sentence reordering (MTLJ-79) is a preserving operator: it moves sentences and changes no
word, so a good judge's score should stay the same. That only holds if no sentence depends on
the one before it. "Preheat the oven. It should reach 200C." reads differently as "It should
reach 200C. Preheat the oven.": "It" now points at nothing, or at the wrong thing, and the
edit has quietly become degrading. This module finds those dependencies so the operator can
leave such sentences where they are.

A sentence **refers back** when it contains any of:

* **A third-person pronoun with no antecedent before it in the same sentence**: he, she, it,
  they, and their other forms (him, its, their, themselves...). "If the fish sticks, turn it"
  resolves "it" inside its own sentence and is fine; "It should reach 200C." does not. An
  expletive "it" ("It is raining", spaCy dependency ``expl``) is not a reference.
* **A demonstrative** used as a determiner or pronoun: this, that, these, those, such ("This
  step", "That is why"). "That" as a conjunction or relative pronoun ("so that", "the pan
  that is hot") is not.
* **A linking word at the start** that only makes sense after another sentence: However, Then,
  Next, Finally, Also, Therefore, Instead, Otherwise, For example, and the like (see
  ``LINKING_STARTS``).
* **An anaphoric word anywhere**: also, too, again, another, former, latter, aforementioned,
  likewise.

The rules are deliberately conservative. A false alarm only keeps a sentence in place and makes
the mutant a little less shuffled; a miss turns a preserving edit into a degrading one and
corrupts the ground truth. The first sentence is checked too: a pronoun there points at the
question, and moving it after another sentence could rebind it.

Keeping a referring sentence in place is not enough on its own: if the sentence it refers to
moves away, the reference breaks just the same. So a referring sentence is **attached** to the
sentence before it, and the two (or a longer chain) only move together. ``ReorderSafety.blocks``
gives these groups; ``ReorderSafety.movable`` gives the sentences that belong to no group and
can move on their own.

**Exclusion rate** is the share of sentences that are not movable on their own. It is reported
for every answer (``ReorderSafety.exclusion_rate``, and in ``summary()`` for a mutant's details)
and for a whole corpus (``exclusion_report``). A high rate means the operator has little to
shuffle in that answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import spacy

# Third-person pronouns that need an antecedent. First and second person ("I", "you", "we")
# point at the speakers, not at an earlier sentence, so moving them changes nothing.
THIRD_PERSON = frozenset(
    {
        "he", "him", "his", "himself",
        "she", "her", "hers", "herself",
        "it", "its", "itself",
        "they", "them", "their", "theirs", "themselves",
    }
)  # fmt: skip

DEMONSTRATIVES = frozenset({"this", "that", "these", "those", "such"})

# Words that, at the start of a sentence, link it to the one before. Lowercase; multi-word
# entries match the sentence's first words.
LINKING_STARTS = (
    "however", "then", "next", "finally", "lastly", "also", "therefore", "thus", "hence",
    "instead", "otherwise", "meanwhile", "afterwards", "afterward", "moreover", "furthermore",
    "additionally", "besides", "consequently", "similarly", "likewise", "still", "second",
    "secondly", "third", "thirdly", "after that", "in addition", "as a result", "for example",
    "for instance", "in contrast", "on the other hand", "in that case", "if so", "if not",
    "once done", "once that", "after this", "before that", "by then", "even so", "that said",
    "this way", "in this way",
)  # fmt: skip

# Words that refer back wherever they appear in a sentence.
ANAPHORIC_WORDS = frozenset(
    {"also", "too", "again", "another", "former", "latter", "aforementioned", "likewise"}
)

# Parts of speech that can be an antecedent for a pronoun later in the same sentence.
_ANTECEDENT_POS = frozenset({"NOUN", "PROPN"})


@lru_cache
def _nlp():
    """Load the spaCy pipeline once and cache it. Tags, dependencies and sentences are used."""
    return spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer"])


@dataclass(frozen=True)
class SentenceSafety:
    """One sentence and whether it refers back to the sentence before it.

    ``reasons`` names each reference found, like ``"pronoun 'It'"`` or ``"linking 'However'"``,
    so an excluded sentence can be explained.
    """

    index: int
    text: str
    reasons: tuple[str, ...]

    @property
    def refers_back(self) -> bool:
        """Whether this sentence depends on an earlier one."""
        return bool(self.reasons)


@dataclass(frozen=True)
class ReorderSafety:
    """The sentences of one answer, and which of them can be reordered safely."""

    sentences: tuple[SentenceSafety, ...]

    @property
    def blocks(self) -> tuple[tuple[int, ...], ...]:
        """Sentence indices grouped so that every group can move as a unit.

        A sentence that refers back joins the group of the sentence before it, so a chain like
        "Preheat the oven. It should be hot. Then add the fish." stays one group. Every other
        sentence is a group of its own, including a first sentence that refers back (to the
        question), which ``movable`` then leaves out. Groups are in their original order.
        """
        groups: list[list[int]] = []
        for sentence in self.sentences:
            if sentence.refers_back and groups:
                groups[-1].append(sentence.index)
            else:
                groups.append([sentence.index])
        return tuple(tuple(g) for g in groups)

    @property
    def movable(self) -> tuple[int, ...]:
        """Indices of sentences that can move on their own.

        A sentence is movable when it forms a group by itself and doesn't refer back. The second
        condition only matters for the first sentence, whose reference points at the question.
        """
        return tuple(
            g[0] for g in self.blocks if len(g) == 1 and not self.sentences[g[0]].refers_back
        )

    @property
    def excluded(self) -> tuple[int, ...]:
        """Indices of sentences that can't move on their own: they refer back or are referred to."""
        movable = set(self.movable)
        return tuple(s.index for s in self.sentences if s.index not in movable)

    @property
    def exclusion_rate(self) -> float | None:
        """The share of sentences that can't move on their own. None for an empty answer."""
        if not self.sentences:
            return None
        return len(self.excluded) / len(self.sentences)

    def summary(self) -> dict[str, Any]:
        """A JSON-ready record for a mutant's ``details``: counts, rate, and the reasons."""
        return {
            "sentences": len(self.sentences),
            "movable": list(self.movable),
            "excluded": list(self.excluded),
            "blocks": [list(b) for b in self.blocks],
            "exclusion_rate": self.exclusion_rate,
            "references": {str(s.index): list(s.reasons) for s in self.sentences if s.reasons},
        }


def _starts_with_link(words: Sequence[str]) -> str | None:
    """The linking phrase the sentence opens with, if any. ``words`` are lowercase."""
    for phrase in LINKING_STARTS:
        parts = phrase.split()
        if list(words[: len(parts)]) == parts:
            return phrase
    return None


def _reasons(sent: Any) -> tuple[str, ...]:
    """Every backward reference in one spaCy sentence span, in reading order."""
    tokens = [t for t in sent if not t.is_space]
    words = [t.lower_ for t in tokens if not t.is_punct]
    reasons: list[str] = []

    link = _starts_with_link(words)
    if link:
        reasons.append(f"linking '{link}'")

    seen_antecedent = False
    for token in tokens:
        lower = token.lower_
        if token.pos_ in _ANTECEDENT_POS:
            seen_antecedent = True
            continue
        if lower in THIRD_PERSON and token.dep_ != "expl" and token.pos_ in {"PRON", "DET"}:
            if not seen_antecedent:
                reasons.append(f"pronoun '{token.text}'")
        elif lower in DEMONSTRATIVES and (token.tag_ in {"DT", "PDT"} or lower == "such"):
            reasons.append(f"demonstrative '{token.text}'")
        elif lower in ANAPHORIC_WORDS and not (link and token.i == tokens[0].i):
            reasons.append(f"anaphoric '{token.text}'")
    return tuple(reasons)


def check_backward_references(text: str) -> ReorderSafety:
    """Split ``text`` into sentences and find the ones that refer back to an earlier sentence.

    Sentences come from spaCy's ``en_core_web_sm`` parser, the same model the other operators
    use. Whitespace-only sentences are dropped.
    """
    doc = _nlp()(text)
    sentences = []
    for sent in doc.sents:
        if not sent.text.strip():
            continue
        sentences.append(SentenceSafety(len(sentences), sent.text.strip(), _reasons(sent)))
    return ReorderSafety(tuple(sentences))


def exclusion_report(results: Iterable[ReorderSafety]) -> dict[str, Any]:
    """The exclusion rate over many answers, for reporting how much an operator can shuffle.

    ``sentence_rate`` pools every sentence; ``mean_answer_rate`` averages each answer's rate, so
    long answers don't dominate. ``no_movable_pairs`` counts answers with fewer than two movable
    sentences, which a reordering operator can't change at all.
    """
    answers = sentences = excluded = no_pairs = 0
    rates: list[float] = []
    for result in results:
        answers += 1
        sentences += len(result.sentences)
        excluded += len(result.excluded)
        if result.exclusion_rate is not None:
            rates.append(result.exclusion_rate)
        if len(result.movable) < 2:
            no_pairs += 1
    return {
        "answers": answers,
        "sentences": sentences,
        "excluded": excluded,
        "sentence_rate": excluded / sentences if sentences else None,
        "mean_answer_rate": sum(rates) / len(rates) if rates else None,
        "no_movable_pairs": no_pairs,
    }
