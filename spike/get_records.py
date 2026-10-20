"""MTLJ-31 spike: pick 20 clean RAGTruth QA records for hand-written mutations.

Writes records.json (the 20 records) and entities.txt (the per-record entity
report that is also printed). No text is modified: context is only split on
the blank lines that separate passages, and that split is asserted lossless.

Run from spike/ with the venv active:
    python get_records.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from functools import lru_cache
from pathlib import Path

DATASET = "wandb/RAGTruth-processed"
MODEL_PREFIX = "gpt-4"
N_RECORDS = 20
MIN_SENTENCES = 3
FALLBACK_MIN_SENTENCES = 2
SEED = 31
HERE = Path(__file__).parent

# Pass the filters but have no usable entity-swap target; skipped by hand.
EXCLUDE = {
    "14088": "only in-context entity is 'one' in the idiom 'from one place to another'",
    "16278": "only in-context entity is 'two' in 'fasten two ropes'; too weak a swap",
}


@lru_cache(maxsize=1)
def nlp():
    """spaCy pipeline, loaded once on first use."""
    import spacy

    return spacy.load("en_core_web_sm")


def split_passages(context: str) -> list[str]:
    """RAGTruth stores QA context as one string with passages separated by blank lines."""
    passages = context.split("\n\n")
    assert "\n\n".join(passages) == context and all(passages), "context split is not lossless"
    return passages


def is_unannotated(record: dict) -> bool:
    """True when the record has no hallucination spans in either label field."""
    spans = json.loads(record["hallucination_labels"] or "[]")
    counts = record["hallucination_labels_processed"] or {}
    return not spans and not any(counts.values())


def in_context(entity: str, context: str) -> bool:
    """Verbatim, case-sensitive match on word boundaries ("one" must not match "someone")."""
    return re.search(rf"(?<!\w){re.escape(entity)}(?!\w)", context) is not None


def load_pool() -> list[dict]:
    """Clean GPT-4 QA records from train+test, in a fixed shuffled order."""
    from datasets import concatenate_datasets, load_dataset

    ds = load_dataset(DATASET)
    every = concatenate_datasets([ds[s] for s in ds])
    pool = [
        r
        for r in every
        if r["task_type"] == "QA"
        and is_unannotated(r)
        and r["model"].startswith(MODEL_PREFIX)
        # Not in the brief: drops 27 incorrect refusals and 1 truncated answer.
        and r["quality"] == "good"
    ]
    random.Random(SEED).shuffle(pool)
    # Exclude after shuffling so excluding a record doesn't reorder the rest.
    return [r for r in pool if r["id"] not in EXCLUDE]


def analyse(record: dict) -> dict:
    """Sentence count and entities (with in-context flag) for one record's answer."""
    doc = nlp()(record["output"])
    return {
        "sentences": len(list(doc.sents)),
        "entities": [(e.text, e.label_, in_context(e.text, record["context"])) for e in doc.ents],
    }


def select(pool: list[dict], min_sentences: int) -> list[tuple[dict, dict]]:
    """First N_RECORDS records meeting the sentence floor with >= 1 entity found in context."""
    chosen = []
    for record in pool:
        info = analyse(record)
        if info["sentences"] >= min_sentences and any(hit for *_, hit in info["entities"]):
            chosen.append((record, info))
            if len(chosen) == N_RECORDS:
                break
    return chosen


def report(chosen: list[tuple[dict, dict]], min_sentences: int) -> str:
    """Human-readable answer + entity list per record; [IN CONTEXT] marks swap candidates."""
    lines = [f"{len(chosen)} records, answers >= {min_sentences} sentences, seed={SEED}"]
    for i, (record, info) in enumerate(chosen, 1):
        lines += [
            "",
            "=" * 100,
            f"[{i}/{len(chosen)}] id={record['id']}  sentences={info['sentences']}",
            f"QUESTION: {record['query']}",
            "",
            f"ANSWER: {record['output']}",
            "",
            "ENTITIES:",
        ]
        for text, label, hit in info["entities"]:
            lines.append(f"  {'[IN CONTEXT]' if hit else '            '}  {label:<12} {text}")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pool = load_pool()

    min_sentences = MIN_SENTENCES
    chosen = select(pool, min_sentences)
    if len(chosen) < N_RECORDS:
        print(
            f"only {len(chosen)} records with >= {MIN_SENTENCES} sentences; "
            f"relaxing to {FALLBACK_MIN_SENTENCES}"
        )
        min_sentences = FALLBACK_MIN_SENTENCES
        chosen = select(pool, min_sentences)
    if len(chosen) < N_RECORDS:
        sys.exit(f"only {len(chosen)} eligible records; need {N_RECORDS}")

    records = [
        {
            "id": r["id"],
            "question": r["query"],
            "context": split_passages(r["context"]),
            "answer": r["output"],
        }
        for r, _ in chosen
    ]
    (HERE / "records.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    text = report(chosen, min_sentences)
    (HERE / "entities.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {len(records)} records to records.json and the entity report to entities.txt")


if __name__ == "__main__":
    main()
