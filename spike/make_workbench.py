"""MTLJ-31 spike: lay out plain-text files for hand-writing mutations.

For each record in records.json, creates workbench/NN_<id>/ containing:
    reference.txt      question, context passages, original answer, entity list (read only)
    entity_swap.txt    starts as an exact copy of the original answer; edit in place
    verbosity_pad.txt  ditto
    reformat.txt       ditto

Never overwrites an existing file, so re-running cannot lose hand-written work.

Run from spike/ with the venv active:
    python make_workbench.py
"""

from __future__ import annotations

import json
from pathlib import Path

from get_records import in_context, nlp

HERE = Path(__file__).parent
WORKBENCH = HERE / "workbench"
TYPES = ("entity_swap", "verbosity_pad", "reformat")


def reference(i: int, total: int, record: dict) -> str:
    """Everything needed to write one record's mutations, in one file."""
    context = "\n\n".join(record["context"])
    lines = [
        f"RECORD {i:02d}/{total}  id={record['id']}",
        "",
        "QUESTION",
        record["question"],
        "",
        "CONTEXT",
    ]
    for n, passage in enumerate(record["context"], 1):
        lines += [f"[P{n}] {passage}", ""]
    lines += [
        "ORIGINAL ANSWER",
        record["answer"],
        "",
        "ENTITIES  (* = appears verbatim in context -> valid swap target)",
    ]
    for ent in nlp()(record["answer"]).ents:
        lines.append(
            f"  {'*' if in_context(ent.text, context) else ' '} {ent.label_:<12} {ent.text}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    records = json.loads((HERE / "records.json").read_text(encoding="utf-8"))
    created = 0
    for i, record in enumerate(records, 1):
        folder = WORKBENCH / f"{i:02d}_{record['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        files = {"reference.txt": reference(i, len(records), record)}
        files.update({f"{t}.txt": record["answer"] for t in TYPES})
        for name, text in files.items():
            path = folder / name
            if not path.exists():
                path.write_text(text, encoding="utf-8", newline="\n")
                created += 1
    print(
        f"{len(records)} record folders in {WORKBENCH}; "
        f"created {created} new files, left existing ones untouched"
    )


if __name__ == "__main__":
    main()
