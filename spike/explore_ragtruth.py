"""MTLJ-31 spike, Task 3: load RAGTruth and inspect the clean QA pool. Read-only.

Run from the repo root:
    spike/.venv/Scripts/python.exe spike/explore_ragtruth.py
"""

from __future__ import annotations

import json
import random
import statistics
from functools import lru_cache

DATASET = "wandb/RAGTruth-processed"
CLEAN_MODEL_PREFIX = "gpt-4"
SEED = 31


@lru_cache(maxsize=1)
def nlp():
    """spaCy pipeline, loaded once on first use."""
    import spacy

    return spacy.load("en_core_web_sm")


def split_passages(context: str) -> list[str]:
    """RAGTruth stores QA context as one string; passages are separated by blank lines."""
    return [p.strip() for p in context.split("\n\n") if p.strip()]


def is_unannotated(record: dict) -> bool:
    """True when the record has no hallucination spans in either label field."""
    spans = json.loads(record["hallucination_labels"] or "[]")
    counts = record["hallucination_labels_processed"] or {}
    return not spans and not any(counts.values())


def load_filter_stages() -> list[tuple[str, list[dict]]]:
    """Return (label, records) after each filter, train+test combined, nothing modified."""
    from datasets import concatenate_datasets, load_dataset

    ds = load_dataset(DATASET)
    every = list(concatenate_datasets([ds[s] for s in ds]))
    qa = [r for r in every if r["task_type"] == "QA"]
    clean = [r for r in qa if is_unannotated(r)]
    gpt4 = [r for r in clean if r["model"].startswith(CLEAN_MODEL_PREFIX)]
    good = [r for r in gpt4 if r["quality"] == "good"]
    return [
        ("all records (train+test)", every),
        ("task_type == QA", qa),
        ("no hallucination annotations", clean),
        (f"model startswith {CLEAN_MODEL_PREFIX!r}", gpt4),
        ("quality == 'good'  [not in brief; see note]", good),
    ]


def describe(name: str, values: list[float]) -> None:
    """Print a one-line distribution summary."""
    qs = statistics.quantiles(values, n=10)
    print(
        f"  {name:<28} n={len(values):<5} min={min(values):<5g} p10={qs[0]:<6g} "
        f"median={statistics.median(values):<6g} p90={qs[-1]:<6g} max={max(values):<6g} "
        f"mean={statistics.fmean(values):.1f}"
    )


def histogram(values: list[int], cap: int) -> None:
    """Print a text histogram, lumping values >= cap together."""
    counts: dict[str, int] = {}
    for v in values:
        key = str(v) if v < cap else f"{cap}+"
        counts[key] = counts.get(key, 0) + 1
    for key in [*map(str, range(min(values), cap)), f"{cap}+"]:
        if key in counts:
            print(f"    {key:>3} | {'#' * max(1, counts[key] // 5)} {counts[key]}")


def main() -> None:
    """Print filter funnel, length distributions, and five full example records."""
    stages = load_filter_stages()

    print("=== filter funnel ===")
    for label, recs in stages:
        print(f"  {len(recs):>6}  {label}")

    from collections import Counter

    qa = stages[1][1]
    clean = stages[2][1]
    print("\n=== breakdowns ===")
    print("  QA by model:            ", dict(Counter(r["model"] for r in qa)))
    print("  clean QA by model:      ", dict(Counter(r["model"] for r in clean)))
    print("  clean GPT-4 by quality: ", dict(Counter(r["quality"] for r in stages[3][1])))
    print(
        "  GPT-4 temperature:      ",
        dict(Counter(round(r["temperature"], 2) for r in stages[3][1])),
    )

    pool = stages[-1][1]
    sents = [len(list(nlp()(r["output"]).sents)) for r in pool]
    print(f"\n=== distributions over final pool (n={len(pool)}) ===")
    describe("answer sentences (spaCy)", sents)
    describe("answer words", [len(r["output"].split()) for r in pool])
    describe("context words", [len(r["context"].split()) for r in pool])
    describe("context passages (\\n\\n)", [len(split_passages(r["context"])) for r in pool])
    describe("query words", [len(r["query"].split()) for r in pool])
    print("\n  answer sentence-count histogram (# = 5 records):")
    histogram(sents, cap=10)
    print(f"\n  answers with >= 3 sentences: {sum(s >= 3 for s in sents)}")

    print(f"\n=== 5 example records (random, seed={SEED}) ===")
    for r in random.Random(SEED).sample(pool, 5):
        passages = split_passages(r["context"])
        print("\n" + "=" * 100)
        print(f"id={r['id']}  model={r['model']}  quality={r['quality']}  passages={len(passages)}")
        print(f"\nQUESTION: {r['query']}")
        for i, p in enumerate(passages, 1):
            print(f"\nPASSAGE {i}: {p}")
        print(f"\nANSWER: {r['output']}")


if __name__ == "__main__":
    main()
