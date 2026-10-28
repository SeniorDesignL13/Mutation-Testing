"""Entity swap operator: replaces a named entity with a different one of the same type.

The answer is mutated so that one or more of its claims contradicts the source
context. The rules follow the feasibility study (docs/spike-effect-size.md):

* Only entities that the context actually supports are targeted, so the claim
  genuinely becomes false.
* The replacement has the same entity type (PERSON, ORG, GPE, DATE) and the same
  format (a year stays a year, a month stays a month).
* The replacement must not appear in any context sentence that mentions the
  original, so another passage cannot support the new value.
* Exactly one span is changed per targeted claim (sentence), never every
  occurrence of the string.
* Entities that appear once in the answer are preferred, so the answer does not
  end up contradicting itself.

Intensity controls how many claims are changed: ``ceil(intensity * 5)``.
Every substitution is recorded in ``details["substitutions"]`` (the provenance).
"""

import logging
import math
import random
import re
from functools import lru_cache

import spacy

from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
    ParameterSpec,
)

logger = logging.getLogger(__name__)

ENTITY_LABELS = ("PERSON", "ORG", "GPE", "DATE")
MAX_SWAPS = 5

# Fallback values, used only when the context holds no usable replacement.
PLACE_POOL = ["Madrid", "Berlin", "Rome", "Vienna", "Warsaw"]
FULL_NAME_POOL = ["Daniel Carter", "Maria Lopez", "James Whitfield", "Priya Nair", "Thomas Becker"]
FIRST_NAME_POOL = ["Daniel", "Maria", "James", "Priya", "Thomas"]
ORG_POOL = ["Northwind Holdings", "Crestline Group", "Harbor Analytics", "Alder Partners"]
POOLS = {
    "GPE": PLACE_POOL,
    "ORG": ORG_POOL,
}

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
MONTH_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\b")


@lru_cache
def _nlp():
    """Load the spaCy pipeline once and cache it."""
    return spacy.load("en_core_web_sm")


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


LINKING_WORDS = {"of", "the", "and", "de", "la", "von", "van", "for"}


def _is_proper(ent) -> bool:
    """True when a PERSON/ORG/GPE span is made of proper nouns.

    The small spaCy model sometimes labels ordinary words ("Cook", "Not") as
    names. Requiring proper-noun tags filters most of those out. DATE spans
    are checked separately.
    """
    if ent.label_ == "DATE":
        return True
    words = [t for t in ent if not t.is_punct]
    if not any(t.pos_ == "PROPN" for t in words):
        return False
    return all(t.pos_ == "PROPN" or t.lower_ in LINKING_WORDS for t in words)


class EntitySwapOperator(MutationOperator):
    """Replaces named entities in the answer so it contradicts its source.

    This makes the text contradict its source, so a good judge's score
    should decrease.
    """

    metadata = OperatorMetadata(
        name="entity_swap",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description=(
            "Replaces named entities (PERSON, ORG, GPE, DATE) that the context "
            "supports with a different entity of the same type, so the answer "
            "contradicts its source. intensity controls how many claims change."
        ),
        parameters=[
            ParameterSpec(
                name="num_swaps",
                description="Maximum number of claims changed, reached at intensity 1.0.",
                default=1,
                min_value=1,
                max_value=MAX_SWAPS,
            )
        ],
    )

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def _context_sentences(context: list[str]) -> list[str]:
        """Normalised sentences of every passage."""
        sentences: list[str] = []
        for passage in context:
            for sent in _nlp()(passage).sents:
                sentences.append(_norm(sent.text))
        return sentences

    @staticmethod
    def _word_in(text: str, haystack: str) -> bool:
        return re.search(r"\b" + re.escape(text) + r"\b", haystack) is not None

    def _context_entities(self, context: list[str], label: str) -> list[str]:
        """Unique entities of ``label`` found in the context passages."""
        found: dict[str, str] = {}
        for passage in context:
            for ent in _nlp()(passage).ents:
                if ent.label_ == label and _is_proper(ent):
                    found.setdefault(_norm(ent.text), ent.text)
        return list(found.values())

    def _valid(
        self,
        replacement: str,
        original: str,
        original_sents: list[str],
        *,
        whole_context: str | None = None,
    ) -> bool:
        """The replacement differs from the original and no supporting sentence has it."""
        rep, orig = _norm(replacement), _norm(original)
        if not rep or rep == orig or rep in orig or orig in rep:
            return False
        if any(self._word_in(rep, sent) for sent in original_sents):
            return False
        return not (whole_context is not None and self._word_in(rep, whole_context))

    def _date_replacement(
        self,
        original: str,
        original_sents: list[str],
        whole_context: str,
        rng: random.Random,
    ) -> str | None:
        """Change the year or the month of a DATE, keeping its format."""
        year = YEAR_RE.search(original)
        if year:
            y = int(year.group(1))
            options = [y + d for d in range(-9, 10) if d != 0 and 1000 <= y + d <= 2099]
            rng.shuffle(options)
            for new_year in options:
                candidate = original[: year.start()] + str(new_year) + original[year.end() :]
                if str(new_year) not in whole_context and self._valid(
                    candidate, original, original_sents
                ):
                    return candidate
            return None
        month = MONTH_RE.search(original)
        if month:
            options = [m for m in MONTHS if m != month.group(1)]
            rng.shuffle(options)
            for new_month in options:
                candidate = original[: month.start()] + new_month + original[month.end() :]
                if not self._word_in(new_month.casefold(), whole_context) and self._valid(
                    candidate, original, original_sents
                ):
                    return candidate
        return None

    def _replacement(
        self,
        ent_text: str,
        label: str,
        context: list[str],
        original_sents: list[str],
        whole_context: str,
        rng: random.Random,
    ) -> tuple[str, str] | None:
        """Return (replacement, source) or None when no valid replacement exists."""
        if label == "DATE":
            new = self._date_replacement(ent_text, original_sents, whole_context, rng)
            return (new, "date_shift") if new else None

        n_words = len(ent_text.split())
        from_context = [
            c
            for c in self._context_entities(context, label)
            if self._valid(c, ent_text, original_sents)
        ]
        same_length = [c for c in from_context if len(c.split()) == n_words]
        pool_choice = same_length or from_context
        if pool_choice:
            return rng.choice(sorted(pool_choice)), "context"

        if label == "PERSON":
            pool = FULL_NAME_POOL if n_words > 1 else FIRST_NAME_POOL
        else:
            pool = POOLS.get(label, [])
        fallback = [
            p for p in pool if self._valid(p, ent_text, original_sents, whole_context=whole_context)
        ]
        return (rng.choice(fallback), "pool") if fallback else None

    # -- main entry point --------------------------------------------------------

    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Swap up to ``ceil(intensity * 5)`` context-supported entities in ``text``."""
        rng = random.Random(seed)
        max_swaps = int(self.metadata.parameters[0].max_value)
        wanted = math.ceil(min(max(intensity, 0.0), 1.0) * max_swaps)

        doc = _nlp()(text)
        sentences = list(doc.sents)
        sent_index = {s.start_char: i for i, s in enumerate(sentences)}
        context_sents = self._context_sentences(context)
        whole_context = _norm(" ".join(context))
        skipped = {"no_context_support": 0, "no_valid_replacement": 0, "not_a_proper_noun": 0}

        # Candidate entities: right type, and supported by the context.
        candidates = []
        for ent in doc.ents:
            if ent.label_ not in ENTITY_LABELS or len(ent.text.strip()) < 3:
                continue
            if not _is_proper(ent):
                skipped["not_a_proper_noun"] += 1
                continue
            if not self._word_in(_norm(ent.text), whole_context):
                skipped["no_context_support"] += 1
                continue
            candidates.append(ent)

        occurrences = {
            _norm(e.text): sum(1 for o in doc.ents if _norm(o.text) == _norm(e.text))
            for e in candidates
        }
        rng.shuffle(candidates)
        candidates.sort(key=lambda e: occurrences[_norm(e.text)] > 1)  # once-only first

        chosen: list[dict] = []
        used_sentences: set[int] = set()
        for ent in candidates:
            if len(chosen) >= wanted:
                break
            idx = sent_index.get(ent.sent.start_char, -1)
            if idx in used_sentences:
                continue
            norm_ent = _norm(ent.text)
            original_sents = [s for s in context_sents if self._word_in(norm_ent, s)]
            found = self._replacement(
                ent.text, ent.label_, context, original_sents, whole_context, rng
            )
            if found is None:
                skipped["no_valid_replacement"] += 1
                continue
            replacement, source = found
            used_sentences.add(idx)
            chosen.append(
                {
                    "original": ent.text,
                    "replacement": replacement,
                    "label": ent.label_,
                    "start": ent.start_char,
                    "end": ent.end_char,
                    "claim_sentence": ent.sent.text.strip(),
                    "claim_sentence_index": idx,
                    "replacement_source": source,
                    "occurrences_in_answer": occurrences[norm_ent],
                }
            )
            logger.debug("entity_swap: %r -> %r (%s)", ent.text, replacement, ent.label_)

        new_text = text
        for sub in sorted(chosen, key=lambda s: s["start"], reverse=True):
            new_text = new_text[: sub["start"]] + sub["replacement"] + new_text[sub["end"] :]

        first = chosen[0] if chosen else None
        return MutationResult(
            original_text=text,
            mutated_text=new_text,
            changed=bool(chosen),
            details={
                "substitutions": sorted(chosen, key=lambda s: s["start"]),
                "requested_swaps": wanted,
                "skipped": skipped,
                "swapped_from": first["original"] if first else None,
                "swapped_to": first["replacement"] if first else None,
            },
        )
