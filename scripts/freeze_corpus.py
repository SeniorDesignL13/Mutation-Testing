"""
MTLJ-44 — Freeze the v1 evaluation corpus.

Reproduces the filter chain validated in the MTLJ-31 spike, writes a frozen
corpus file, a SHA-256 checksum, and a manifest describing how it was built.

Run from the repo root:
    python scripts/freeze_corpus.py

Outputs:
    corpus/v1/corpus_v1.jsonl      one record per line, sorted by id
    corpus/v1/corpus_v1.sha256     checksum of the above
    corpus/v1/manifest.json        filter chain, counts, provenance

Deterministic: records are sorted by id before writing, so re-running on the
same dataset version produces a byte-identical file and the same digest.

Schema notes (wandb/RAGTruth-processed, verified 2026-10-19):
    id, query, context, output, task_type, quality, model, temperature,
    hallucination_labels, hallucination_labels_processed, input_str

    hallucination_labels is a JSON STRING: "[]" when the record is clean,
    otherwise a list of annotated spans. hallucination_labels_processed is a
    dict present on every record: {"evident_conflict": N, "baseless_info": N}.
    Both are truthy when clean, so cleanliness must be tested by VALUE.
"""

import hashlib
import json
import os
from datetime import date

try:
    from datasets import load_dataset
except ImportError:
    raise SystemExit("pip install datasets") from None

SOURCE = "wandb/RAGTruth-processed"
TARGET_MODEL = "gpt-4-0613"

OUT_DIR = os.path.join("corpus", "v1")
CORPUS_PATH = os.path.join(OUT_DIR, "corpus_v1.jsonl")
CHECKSUM_PATH = os.path.join(OUT_DIR, "corpus_v1.sha256")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")

# The 20 records hand-mutated and human-reviewed during the MTLJ-31 spike.
# Tagged in the corpus as the gold set for validating the automated
# entity-substitution operator (MTLJ-60).
GOLD_SET_IDS = {
    "11868",
    "12018",
    "12660",
    "12852",
    "13122",
    "13302",
    "13560",
    "14352",
    "14922",
    "15204",
    "15450",
    "16080",
    "16980",
    "17028",
    "17148",
    "17388",
    "17472",
    "17490",
    "17592",
    "17622",
}


def is_clean(rec):
    """
    True when human annotators marked no hallucinated spans.

    hallucination_labels is a JSON string; "[]" means no annotations.
    We cross-check the processed counts, which must both be zero.
    """
    raw = (rec.get("hallucination_labels") or "").strip()
    if raw not in ("", "[]"):
        return False
    proc = rec.get("hallucination_labels_processed") or {}
    return not (proc.get("evident_conflict") or proc.get("baseless_info"))


def split_context(raw):
    """RAGTruth QA context is passages joined by blank lines. Lossless split."""
    if isinstance(raw, list):
        return [p for p in raw if p and p.strip()]
    if not raw:
        return []
    return [p.strip() for p in str(raw).split("\n\n") if p.strip()]


def main():
    print(f"Loading {SOURCE} ...")
    ds = load_dataset(SOURCE)

    n_scanned = n_qa = n_clean = n_model = n_quality = 0
    records = []

    for split_name in ds:
        for rec in ds[split_name]:
            n_scanned += 1

            if rec.get("task_type") != "QA":
                continue
            n_qa += 1

            if not is_clean(rec):
                continue
            n_clean += 1

            if rec.get("model") != TARGET_MODEL:
                continue
            n_model += 1

            if rec.get("quality") != "good":
                continue
            n_quality += 1

            rid = str(rec.get("id") or "")
            question = (rec.get("query") or "").strip()
            answer = (rec.get("output") or "").strip()
            context = split_context(rec.get("context"))

            if not (rid and question and answer and context):
                continue

            records.append(
                {
                    "id": rid,
                    "question": question,
                    "context": context,
                    "answer": answer,
                    "split": split_name,
                    "model": rec.get("model"),
                    "gold_set": rid in GOLD_SET_IDS,
                }
            )

    # Deterministic order -> stable checksum
    records.sort(key=lambda r: r["id"])

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(CORPUS_PATH, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    h = hashlib.sha256()
    with open(CORPUS_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()

    with open(CHECKSUM_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{digest}  corpus_v1.jsonl\n")

    gold_found = sum(1 for r in records if r["gold_set"])
    answer_words = sorted(len(r["answer"].split()) for r in records)
    ctx_chars = sorted(sum(len(p) for p in r["context"]) for r in records)
    passages = sorted(len(r["context"]) for r in records)

    def median(xs):
        return xs[len(xs) // 2] if xs else 0

    manifest = {
        "corpus_version": "v1",
        "frozen_on": date.today().isoformat(),
        "source_dataset": SOURCE,
        "license": "MIT",
        "sha256": digest,
        "record_count": len(records),
        "gold_set_count": gold_found,
        "filter_chain": [
            {"step": "all records scanned", "remaining": n_scanned},
            {"step": "task_type == QA", "remaining": n_qa},
            {"step": "no hallucination annotations", "remaining": n_clean},
            {"step": f"model == {TARGET_MODEL}", "remaining": n_model},
            {"step": "quality == good", "remaining": n_quality},
            {"step": "non-empty question/answer/context", "remaining": len(records)},
        ],
        "composition": {
            "median_answer_words": median(answer_words),
            "min_answer_words": answer_words[0] if answer_words else 0,
            "max_answer_words": answer_words[-1] if answer_words else 0,
            "median_context_chars": median(ctx_chars),
            "min_passages": passages[0] if passages else 0,
            "max_passages": passages[-1] if passages else 0,
            "median_passages": median(passages),
        },
        "sampling_policy": (
            "Audits draw a seeded sample from this pool rather than using it "
            "whole. The seed is recorded in run provenance so any audit is "
            "reproducible from its manifest."
        ),
    }

    with open(MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    c = manifest["composition"]
    print()
    print("=" * 58)
    print("FILTER CHAIN")
    for s in manifest["filter_chain"]:
        print(f"  {s['step']:42s} {s['remaining']:>8,}")
    print("=" * 58)
    print(f"  records frozen        {len(records):,}")
    print(f"  gold set found        {gold_found}/{len(GOLD_SET_IDS)}")
    print(
        f"  answer words          median {c['median_answer_words']}  "
        f"range {c['min_answer_words']}-{c['max_answer_words']}"
    )
    print(f"  context chars         median {c['median_context_chars']:,}")
    print(
        f"  passages per record   median {c['median_passages']}  "
        f"range {c['min_passages']}-{c['max_passages']}"
    )
    print(f"  sha256                {digest}")
    print()
    print(f"Wrote {CORPUS_PATH}")
    print(f"Wrote {CHECKSUM_PATH}")
    print(f"Wrote {MANIFEST_PATH}")

    if gold_found != len(GOLD_SET_IDS):
        missing = sorted(GOLD_SET_IDS - {r["id"] for r in records})
        print(f"\n  WARNING: {gold_found}/{len(GOLD_SET_IDS)} gold-set records found.")
        print(f"  Missing: {missing}")

    if len(records) != 919:
        print(f"\n  NOTE: {len(records)} records; the MTLJ-31 spike reported 919.")


if __name__ == "__main__":
    main()
