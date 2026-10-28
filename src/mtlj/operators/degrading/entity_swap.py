"""Entity swap operator: replaces a named entity with a contradicting one."""

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

PLACE_POOL = ["Madrid", "Berlin", "Rome", "Vienna", "Warsaw"]


@lru_cache
def _nlp():
    """Load the spaCy pipeline once and cache it."""
    return spacy.load("en_core_web_sm")


class EntitySwapOperator(MutationOperator):
    """Replaces a named place entity with a different one of the same type.

    This makes the text contradict its source, so a good judge's score
    should decrease.
    """

    metadata = OperatorMetadata(
        name="entity_swap",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description=(
            "Replaces a named entity (e.g. a place) with a different "
            "entity of the same type, so the text contradicts its source."
        ),
        parameters=[
            ParameterSpec(
                name="num_swaps",
                description="How many entities to swap.",
                default=1,
                min_value=1,
                max_value=5,
            )
        ],
    )

    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Swap the first place entity found in ``text`` for a different one."""
        rng = random.Random(seed)
        doc = _nlp()(text)
        new_text = text
        changed = False
        swapped_from, swapped_to = None, None

        for ent in doc.ents:
            if ent.label_ == "GPE":
                candidates = [p for p in PLACE_POOL if p != ent.text]
                replacement = rng.choice(candidates)
                new_text = new_text.replace(ent.text, replacement)
                swapped_from, swapped_to = ent.text, replacement
                changed = True
                logger.debug("entity_swap: swapped %r -> %r", ent.text, replacement)
                break

        return MutationResult(
            original_text=text,
            mutated_text=new_text,
            changed=changed,
            details={"swapped_from": swapped_from, "swapped_to": swapped_to},
        )
