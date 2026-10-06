"""Verbosity padding operator: adds filler without introducing new claims.

The safest way to guarantee "no new claims" is to never invent new
content. Padding is drawn only from:
  (a) phrases already present in the source context, restated, or
  (b) a small, hand-reviewed list of meaning-free connective phrases.

Every word added either already appeared in the source or comes from a
fixed list of filler phrases that carry no factual content, so this
operator cannot smuggle in a claim.
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
            "context or inserting meaning-free connective phrases."
        ),
        parameters=[
            ParameterSpec(
                name="words_added",
                description="Approximate number of filler words to add.",
                default=50,
                min_value=10,
                max_value=400,
            )
        ],
    )

    def _extract_source_phrases(self, context: list[str]) -> list[str]:
        """Pull noun phrases already present in the source context.

        Filters out bare pronouns (spaCy sometimes tags "that" or "it" as
        a noun chunk) and very short chunks, since these produce filler
        that reads unnaturally (e.g. "this concerns that.").
        """
        phrases = []
        for passage in context:
            doc = _nlp()(passage)
            for chunk in doc.noun_chunks:
                text = chunk.text.strip()
                is_bare_pronoun = len(chunk) == 1 and chunk[0].pos_ == "PRON"
                if text and not is_bare_pronoun and len(text) > 2:
                    phrases.append(text)
        return phrases

    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Append filler sentences to ``text`` until the target word count is hit."""
        rng = random.Random(seed)
        target_words = int(self.metadata.parameters[0].default * intensity)
        source_phrases = self._extract_source_phrases(context)

        filler_parts: list[str] = []
        words_so_far = 0
        attempts = 0

        while words_so_far < target_words and attempts < 50:
            attempts += 1
            connector = rng.choice(NEUTRAL_CONNECTORS)

            if source_phrases:
                phrase = rng.choice(source_phrases)
                sentence = f"{connector} this concerns {phrase}."
            else:
                sentence = f"{connector.capitalize()} this remains the case."

            filler_parts.append(sentence)
            words_so_far += len(sentence.split())

        mutated_text = text.rstrip() + " " + " ".join(filler_parts)
        logger.debug("verbosity_padding: added %d words", words_so_far)

        return MutationResult(
            original_text=text,
            mutated_text=mutated_text,
            changed=len(filler_parts) > 0,
            details={
                "words_added": words_so_far,
                "target_words": target_words,
                "num_filler_sentences": len(filler_parts),
                "source_phrases_available": len(source_phrases),
            },
        )
