"""Verbosity padding operator: adds filler without introducing new claims.

The safest way to guarantee "no new claims" is to never invent new
content. Padding is drawn only from:
  (a) phrases already present in the source context, restated, or
  (b) a small, hand-reviewed list of meaning-free connective phrases and
      sentence stems.

Every word added either already appeared in the source or comes from a
fixed list of filler phrases that carry no factual content, so this
operator cannot smuggle in a claim.

Intensity maps linearly onto the number of words added:
``words = round(intensity * 400)``. The four study levels are therefore
intensity 0.125 / 0.25 / 0.5 / 1.0 for 50 / 100 / 200 / 400 words. Use
``intensity_for_words`` to convert.
"""

import logging
import random
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

# The padding levels (extra words) used to build the dose-response curve.
PAD_LEVELS = (50, 100, 200, 400)
MAX_PAD_WORDS = 400
# The added word count must land within this fraction of the target.
TOLERANCE = 0.10

# Hand-reviewed, meaning-free connective phrases. None of these assert a
# fact, so they cannot smuggle in a new claim regardless of where inserted.
NEUTRAL_CONNECTORS = [
    "in other words,",
    "to put it another way,",
    "as mentioned,",
    "more specifically,",
    "that said,",
    "in general,",
    "as a result,",
    "overall,",
]

# Templates that only *mention* a phrase taken from the source context.
MENTION_TEMPLATES = [
    "{connector} this concerns {phrase}.",
    "{connector} this is about {phrase}.",
    "{connector} this touches on {phrase}.",
    "{connector} the answer mentions {phrase}.",
]

# Meaning-free sentence stems that talk about the response itself, not the
# topic. Used on their own when the context gives no usable phrases, and
# mixed in for variety otherwise.
NEUTRAL_STEMS = [
    "this is worth keeping in mind.",
    "the above is stated as clearly as possible.",
    "please read the above with that in mind.",
    "this is simply a restatement of the same answer.",
    "nothing further is meant by this.",
    "the details above are given for completeness.",
    "this is included only for clarity.",
    "it helps to read the answer once more.",
]


def intensity_for_words(words: int) -> float:
    """Return the intensity that yields ``words`` filler words."""
    return words / MAX_PAD_WORDS


@lru_cache
def _nlp():
    """Load the spaCy pipeline once and cache it."""
    return spacy.load("en_core_web_sm")


class VerbosityPaddingOperator(MutationOperator):
    """Adds filler words to text without introducing any new claim.

    Filler is built from noun phrases that already appear in the source
    context (so nothing new is claimed) plus meaning-free connector
    phrases, so a good judge's score should stay unchanged.
    """

    metadata = OperatorMetadata(
        name="verbosity_padding",
        family=OperatorFamily.PRESERVING,
        expected_direction=ExpectedDirection.UNCHANGED,
        description=(
            "Adds filler words to the text without introducing any new "
            "claim, by restating words already present in the source "
            "context or inserting meaning-free connective phrases. "
            "intensity * 400 filler words are added (50/100/200/400)."
        ),
        parameters=[
            ParameterSpec(
                name="words_added",
                description="Filler words added at intensity 1.0 (intensity scales this).",
                default=MAX_PAD_WORDS,
                min_value=10,
                max_value=MAX_PAD_WORDS,
            )
        ],
    )

    def _extract_source_phrases(self, context: list[str]) -> list[str]:
        """Pull unique noun phrases already present in the source context.

        Skips bare pronouns (spaCy sometimes tags "that" or "it" as a noun
        chunk), very short chunks, and chunks containing digits, so the
        filler never repeats a number from the source.
        """
        phrases: list[str] = []
        seen: set[str] = set()
        for passage in context:
            doc = _nlp()(passage)
            for chunk in doc.noun_chunks:
                text = chunk.text.strip()
                is_bare_pronoun = len(chunk) == 1 and chunk[0].pos_ == "PRON"
                has_digit = any(ch.isdigit() for ch in text)
                if not text or is_bare_pronoun or has_digit or len(text) <= 2:
                    continue
                key = text.lower()
                if key not in seen:
                    seen.add(key)
                    phrases.append(text)
        return phrases

    def _candidate(self, rng: random.Random, phrases: list[str]) -> str:
        """Build one filler sentence."""
        connector = rng.choice(NEUTRAL_CONNECTORS)
        if phrases and rng.random() < 0.75:
            template = rng.choice(MENTION_TEMPLATES)
            sentence = template.format(connector=connector, phrase=rng.choice(phrases))
            return sentence[0].upper() + sentence[1:]
        stem = rng.choice(NEUTRAL_STEMS)
        return f"{connector.capitalize()} {stem}"

    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Append filler sentences so that about ``intensity * 400`` words are added."""
        rng = random.Random(seed)
        max_words = int(self.metadata.parameters[0].max_value)
        target_words = round(min(max(intensity, 0.0), 1.0) * max_words)
        phrases = self._extract_source_phrases(context)

        filler: list[str] = []
        used: set[str] = set()
        words_so_far = 0

        # Greedy fill: each round, draw a few candidates and keep the one
        # that lands the running total closest to the target. Stop when no
        # candidate gets closer.
        for _ in range(500):
            if words_so_far >= target_words:
                break
            options = [self._candidate(rng, phrases) for _ in range(12)]
            options = [s for s in options if s not in used] or options
            best = min(
                options,
                key=lambda s: abs(target_words - (words_so_far + len(s.split()))),
            )
            new_total = words_so_far + len(best.split())
            if abs(target_words - new_total) >= abs(target_words - words_so_far):
                break
            filler.append(best)
            used.add(best)
            words_so_far = new_total

        # With no filler the text comes back untouched (not rstripped), so it isn't "changed".
        mutated_text = text.rstrip() + " " + " ".join(filler) if filler else text
        within = target_words == 0 or abs(words_so_far - target_words) <= TOLERANCE * target_words
        logger.debug("verbosity_padding: added %d of %d words", words_so_far, target_words)

        return MutationResult(
            original_text=text,
            mutated_text=mutated_text,
            changed=len(filler) > 0,
            details={
                "words_added": words_so_far,
                "target_words": target_words,
                "within_tolerance": within,
                "num_filler_sentences": len(filler),
                "source_phrases_available": len(phrases),
            },
        )
