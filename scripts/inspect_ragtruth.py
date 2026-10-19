"""Dump RAGTruth's real schema so we can fix the freeze filters."""

import json
from collections import Counter

from datasets import load_dataset

ds = load_dataset("wandb/RAGTruth-processed")

print("SPLITS:", {k: len(v) for k, v in ds.items()})
first = list(ds.keys())[0]
cols = ds[first].column_names
print("\nCOLUMNS:", cols)

# Grab a few QA records
qa = []
for split in ds:
    for rec in ds[split]:
        t = rec.get("task_type") or rec.get("task")
        if t == "QA":
            qa.append(rec)
            if len(qa) >= 5:
                break
    if len(qa) >= 5:
        break

print("\n" + "=" * 60)
print("SAMPLE QA RECORD — every field, truncated to 300 chars")
print("=" * 60)
for k, v in qa[0].items():
    s = json.dumps(v, default=str)
    print(f"\n--- {k}  ({type(v).__name__}) ---")
    print(s[:300] + ("..." if len(s) > 300 else ""))

# Value distributions on the fields the filters care about
print("\n" + "=" * 60)
print("VALUE DISTRIBUTIONS across all QA records")
print("=" * 60)

interesting = [c for c in cols if any(
    k in c.lower() for k in
    ("label", "halluc", "quality", "model", "conflict", "baseless", "span", "annot", "type")
)]
print("fields examined:", interesting)

allqa = []
for split in ds:
    for rec in ds[split]:
        t = rec.get("task_type") or rec.get("task")
        if t == "QA":
            allqa.append(rec)
print(f"QA records: {len(allqa)}")

for c in interesting:
    vals = Counter()
    for rec in allqa:
        v = rec.get(c)
        if isinstance(v, (list, dict)):
            vals[f"<{type(v).__name__} len={len(v)}>"] += 1
        else:
            vals[repr(v)[:60]] += 1
    print(f"\n--- {c} ---")
    for val, n in vals.most_common(8):
        print(f"   {n:>6,}  {val}")

# Which field looks like the record id?
print("\n" + "=" * 60)
print("ID CANDIDATES — do any contain 13122 (a spike record)?")
print("=" * 60)
for c in cols:
    sample = [str(r.get(c)) for r in allqa[:200]]
    if any(s == "13122" for s in (str(r.get(c)) for r in allqa)):
        print(f"   {c}  <-- MATCH")
    elif sample and all(s.isdigit() for s in sample if s not in ("None", "")):
        print(f"   {c}  (numeric, e.g. {sample[0]})")
