"""Negation insertion operator: negates claims the source supports, so the answer contradicts it.

The answer is mutated so that one or more of its claims says the opposite of its context. The
rules:

* Each sentence's main verb is found with spaCy's dependency parse, and "not" goes where
  English puts it:

  - after the first helper verb: ``is built`` -> ``is not built``, ``has been`` -> ``has not
    been``, ``will open`` -> ``will not open``, and ``can`` -> ``cannot``
  - after a form of "be" used as the main verb: ``is 324 metres tall`` -> ``is not 324 ...``
  - otherwise with "do": ``opened`` -> ``did not open``, ``runs`` -> ``does not run``,
    ``have`` -> ``do not have``

* A sentence that already has a negation anywhere in it (``not``, ``n't``, ``never``, ``no``,
  ``nothing``, ``without``...) is never negated again, so negations never stack and a double
  negative can't turn the claim back into a true one.
* Only claims are negated: a sentence needs a subject, and questions, commands and verbless
  fragments are left alone.
* Only claims the context supports are targeted: most of the sentence's content words must
  appear in the context, so negating it really contradicts the source.
* At most one negation per sentence.

Intensity controls how many claims are negated: ``ceil(intensity * 5)``. Every negation is
recorded in ``details["substitutions"]`` (the provenance), like entity swap's, so the validity
filter and the dose deduplication in ``generate_mutants`` see it.
"""

import math
import random
import re

from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
    ParameterSpec,
)
from mtlj.operators.degrading.entity_swap import _nlp

MAX_NEGATIONS = 5
# Share of a sentence's content words the context must hold for the claim to count as stated.
MIN_SUPPORT = 0.6
MIN_CONTENT_WORDS = 1

NEGATORS = {
    "not",
    "n't",
    "never",
    "no",
    "none",
    "nobody",
    "nothing",
    "nowhere",
    "neither",
    "nor",
    "without",
    "cannot",
    "hardly",
    "barely",
    "scarcely",
}
SUBJECTS = {"nsubj", "nsubjpass", "expl", "csubj", "csubjpass"}
HELPERS = {"aux", "auxpass"}
CONTENT_POS = {"NOUN", "PROPN", "VERB", "ADJ", "NUM", "ADV"}
# The form of "do" that carries a negated verb's tense and person.
DO_FORMS = {"VBZ": "does", "VBP": "do", "VBD": "did"}
FINITE_TAGS = {"VBZ", "VBP", "VBD", "MD"}
# "X may cause Y" and "X may not cause Y" can both be true, so these don't make a contradiction.
UNCERTAIN_MODALS = {"may", "might"}
# Contracted helpers. "'s", "'re" and "'m" take "not" well ("it's not"); the others are spelled
# out first ("you'll" -> "you will not").
CONTRACTIONS = {"'ll", "'ve", "'d"}
SPELLED_OUT = {"'ll": "will", "'ve": "have"}
CURLY_APOSTROPHE = "\u2019"
WORD_RE = re.compile(r"\w+")


def _is_negated(sent) -> bool:
    """True when the sentence already holds a negation, so a new one would stack on it."""
    return any(t.dep_ == "neg" or t.lower_ in NEGATORS for t in sent)


def _supported(sent, context_words: set[str]) -> bool:
    """True when most of the sentence's content words appear in the context."""
    content = [
        t for t in sent if t.pos_ in CONTENT_POS and not t.is_stop and (t.is_alpha or t.like_num)
    ]
    if len(content) < MIN_CONTENT_WORDS:
        return False
    found = sum(t.lower_ in context_words or t.lemma_.lower() in context_words for t in content)
    return found / len(content) >= MIN_SUPPORT


def _negation(sent) -> dict | None:
    """Where and how to negate the sentence's main claim, or None when it can't be.

    Returns the token to replace and its replacement, e.g. ``opened`` -> ``did not open``.
    """
    root = sent.root
    if root.pos_ not in ("VERB", "AUX") or sent.text.rstrip().endswith("?"):
        return None
    if not any(child.dep_ in SUBJECTS for child in root.children):
        return None  # a command or a fragment, not a claim
    helpers = sorted((c for c in root.children if c.dep_ in HELPERS), key=lambda t: t.i)
    if helpers:
        anchor = helpers[0]
        if anchor.tag_ not in FINITE_TAGS or anchor.lower_ in UNCERTAIN_MODALS:
            return None  # "to be built" has no finite verb; "may not" doesn't contradict "may"
        if anchor.lemma_.lower() == "get" and anchor.tag_ in DO_FORMS:
            # "got married" -> "did not get married", not "got not married".
            replacement, form = f"{DO_FORMS[anchor.tag_]} not get", "do"
        else:
            joiner = "" if anchor.lower_ == "can" else " "  # "cannot" is one word
            replacement, form = anchor.text + joiner + "not", "helper"
    elif root.lemma_ == "be" and root.tag_ in FINITE_TAGS:
        anchor, replacement, form = root, root.text + " not", "be"
    elif root.pos_ == "VERB" and root.tag_ in DO_FORMS:
        anchor, form = root, "do"
        replacement = f"{DO_FORMS[root.tag_]} not {root.lemma_.lower()}"
    else:
        return None
    if anchor.i == sent.start:
        return None  # an inverted sentence ("Had I known..."), not a plain claim
    start, original = anchor.idx, anchor.text
    contraction = anchor.lower_.replace(CURLY_APOSTROPHE, "'")
    if contraction in CONTRACTIONS:
        # "you'll need" -> "you will not need": "you'll not need" reads oddly. The word the
        # contraction hangs on is replaced too, so the change is one span.
        if contraction not in SPELLED_OUT:
            return None  # "'d" is "had" or "would": which one isn't known
        host = anchor.doc[anchor.i - 1]
        start = host.idx
        original = anchor.doc.text[start : anchor.idx + len(anchor.text)]
        replacement = f"{host.text} {SPELLED_OUT[contraction]} not"
    return {
        "original": original,
        "replacement": replacement,
        "form": form,
        "verb": root.text,
        "start": start,
        "end": anchor.idx + len(anchor.text),
        "claim_sentence": sent.text.strip(),
    }


class NegationInsertionOperator(MutationOperator):
    """Negates claims in the answer that the context supports, so it contradicts its source.

    This makes the text contradict its source, so a good judge's score should decrease.
    """

    metadata = OperatorMetadata(
        name="negation_insertion",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description=(
            "Inserts a negation at the main verb of claims the context supports (is -> is "
            "not, opened -> did not open), skipping sentences that are already negated. "
            "intensity controls how many claims are negated."
        ),
        version=1,
        parameters=[
            ParameterSpec(
                name="num_negations",
                description="Maximum number of claims negated, reached at intensity 1.0.",
                default=1,
                min_value=1,
                max_value=MAX_NEGATIONS,
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
        """Negate up to ``ceil(intensity * 5)`` context-supported claims in ``text``."""
        rng = random.Random(seed)
        wanted = math.ceil(min(max(intensity, 0.0), 1.0) * MAX_NEGATIONS)
        context_words = {w.lower() for passage in context for w in WORD_RE.findall(passage)}
        skipped = {"already_negated": 0, "not_a_claim": 0, "no_context_support": 0}

        candidates = []
        for index, sent in enumerate(_nlp()(text).sents):
            if _is_negated(sent):
                skipped["already_negated"] += 1
                continue
            negation = _negation(sent)
            if negation is None:
                skipped["not_a_claim"] += 1
                continue
            if not _supported(sent, context_words):
                skipped["no_context_support"] += 1
                continue
            candidates.append({**negation, "claim_sentence_index": index})

        rng.shuffle(candidates)
        chosen = sorted(candidates[:wanted], key=lambda c: c["start"])
        mutated = text
        for sub in reversed(chosen):
            mutated = mutated[: sub["start"]] + sub["replacement"] + mutated[sub["end"] :]
        return MutationResult(
            original_text=text,
            mutated_text=mutated,
            changed=bool(chosen),
            details={
                "substitutions": chosen,
                "requested_negations": wanted,
                "candidates": len(candidates),
                "skipped": skipped,
            },
        )
