"""MTLJ-31 spike: collect hand-written mutations from workbench/ into mutants.json and check them.

Only reads and compares text; it never writes or alters a mutation. A file still
identical to the original answer counts as not started and is left out, so this
can be run at any point to see progress and catch mistakes early.

Checks (warnings, not errors; you decide):
    entity_swap    exactly one changed span; the old value is in the context, the new one is not
    verbosity_pad  insertions only (original wording untouched); ~100 words; no new names/numbers
    reformat       every line a '- ' bullet; minus bullets, the words match the original exactly

Run from spike/ with the venv active:
    python build_mutants.py
"""

from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from get_records import in_context, nlp

HERE = Path(__file__).parent
WORKBENCH = HERE / "workbench"
TYPES = ("entity_swap", "verbosity_pad", "reformat")
PAD_WORDS = (70, 130)
EDGE_PUNCT = ".,;:!?\"'()[]’‘“”"  # noqa: RUF001 - curly quotes are intentional
BULLET = re.compile(r"^[ \t]*- ", re.MULTILINE)


def read_mutant(path: Path) -> str | None:
    """Edited text with editor artifacts (BOM, CRLF, trailing newline) removed; None if missing."""
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()


def diff_ops(original: str, mutant: str, merge_gap: int = 0) -> list[tuple[str, str, str]]:
    """Whitespace-token diff: (tag, original span, mutant span) for each region that differs.

    Regions separated by at most merge_gap unchanged tokens are merged, so a range
    swap like "2 to 3" -> "4 to 5" counts as one change rather than two.
    """
    a, b = original.split(), mutant.split()
    regions: list[list] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if regions and i1 - regions[-1][2] <= merge_gap and j1 - regions[-1][4] <= merge_gap:
            regions[-1][0], regions[-1][2], regions[-1][4] = "replace", i2, j2
        else:
            regions.append([tag, i1, i2, j1, j2])
    return [(tag, " ".join(a[i1:i2]), " ".join(b[j1:j2])) for tag, i1, i2, j1, j2 in regions]


def new_facts(original: str, mutant: str) -> list[str]:
    """Names and numbers in the mutant that aren't in the original: likely new claims."""
    doc = nlp()(mutant)
    candidates = [e.text for e in doc.ents] + [
        t.text for t in doc if t.pos_ == "PROPN" or t.like_num
    ]
    cores = {c.strip("-•* \n" + EDGE_PUNCT) for c in candidates}
    return sorted(c for c in cores if c and c not in original)


def clip(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def check_swap(original: str, mutant: str, context: str) -> tuple[list[dict], list[str]]:
    ops = diff_ops(original, mutant, merge_gap=2)
    swaps = [{"from": old, "to": new} for _, old, new in ops]
    warns = []
    if not ops:
        warns.append("only whitespace changed")
    if len(ops) > 1:
        warns.append(f"{len(ops)} separate changes; should be exactly one swap")
    for s in swaps:
        old, new = s["from"].strip(EDGE_PUNCT), s["to"].strip(EDGE_PUNCT)
        if not old or not in_context(old, context):
            warns.append(
                f"'{s['from']}' is not verbatim in the context, so the swap doesn't contradict it"
            )
        if new and in_context(new, context):
            warns.append(
                f"'{s['to']}' also appears in the context; make sure the swap still contradicts it"
            )
    return swaps, warns


def check_pad(original: str, mutant: str) -> list[str]:
    warns = [
        f"original text changed: '{clip(old)}' -> '{clip(new)}'"
        for tag, old, new in diff_ops(original, mutant)
        if tag != "insert"
    ]
    added = len(mutant.split()) - len(original.split())
    if not PAD_WORDS[0] <= added <= PAD_WORDS[1]:
        warns.append(f"added {added} words; target ~100")
    if facts := new_facts(original, mutant):
        warns.append(f"new names/numbers, check they aren't new claims: {', '.join(facts)}")
    return warns


def check_reformat(original: str, mutant: str) -> list[str]:
    """With the bullet markers removed, the words must match the original exactly, in order."""
    lines = [line for line in mutant.splitlines() if line.strip()]
    warns = []
    if sum(bool(BULLET.match(line)) for line in lines) < 2:
        warns.append("fewer than 2 '- ' bullet lines")
    if unbulleted := [line for line in lines if not BULLET.match(line)]:
        warns.append(
            f"{len(unbulleted)} line(s) without a '- ' bullet, e.g. '{clip(unbulleted[0])}'"
        )
    warns += [
        f"wording differs from the original: '{clip(old)}' -> '{clip(new)}'"
        for _, old, new in diff_ops(original, BULLET.sub("", mutant))
    ]
    return warns


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    records = json.loads((HERE / "records.json").read_text(encoding="utf-8"))
    mutants, rows, details = [], [], []

    for i, record in enumerate(records, 1):
        folder = WORKBENCH / f"{i:02d}_{record['id']}"
        original, context = record["answer"], "\n\n".join(record["context"])
        marks = []
        for mtype in TYPES:
            text = read_mutant(folder / f"{mtype}.txt")
            if text is None or text == original:
                marks.append("--")
                continue
            entry = {"record_id": record["id"], "mutation_type": mtype, "text": text}
            if mtype == "entity_swap":
                entry["swap"], warns = check_swap(original, text, context)
                details += [f"{i:02d} swap: '{s['from']}' -> '{s['to']}'" for s in entry["swap"]]
            elif mtype == "verbosity_pad":
                warns = check_pad(original, text)
            else:
                warns = check_reformat(original, text)
            details += [f"{i:02d} {mtype} WARN: {w}" for w in warns]
            marks.append("!!" if warns else "ok")
            mutants.append(entry)
        rows.append(f"  {i:02d}  {record['id']:<6}  " + "  ".join(f"{m:<13}" for m in marks))

    print("   #  id      " + "  ".join(f"{t:<13}" for t in TYPES))
    print("\n".join(rows))
    per_type = ", ".join(f"{t} {sum(m['mutation_type'] == t for m in mutants)}" for t in TYPES)
    print(
        f"\ndone {len(mutants)}/{len(records) * len(TYPES)}  ({per_type})   "
        "ok = clean, !! = see below, -- = not started"
    )
    if details:
        print("\n" + "\n".join(details))

    (HERE / "mutants.json").write_text(
        json.dumps(mutants, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote {len(mutants)} mutations to mutants.json")


if __name__ == "__main__":
    main()
