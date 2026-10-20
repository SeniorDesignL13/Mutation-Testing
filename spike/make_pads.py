"""MTLJ-31 spike: apply one hand-written filler text to every answer as its verbosity pad.

Reads pad_filler.txt (written by hand, once) and writes
    workbench/NN_<id>/verbosity_pad.txt = BEFORE + " " + original answer + " " + AFTER
for every record, so all 20 answers get the identical padding. Deterministic: no
text is generated here, it is only concatenated.

Refuses filler containing names or numbers. Only writes pad files that are still the
untouched original (or already hold exactly this output); hand-edited pads are skipped
unless --force.

Run from spike/ with the venv active, then check with build_mutants.py:
    python make_pads.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from build_mutants import WORKBENCH, new_facts, read_mutant

HERE = Path(__file__).parent
TARGET_WORDS = (90, 110)


def load_filler() -> tuple[str, str]:
    """(before, after) from pad_filler.txt, comments dropped and line breaks joined with spaces."""
    text = "\n".join(
        line
        for line in (HERE / "pad_filler.txt").read_text(encoding="utf-8-sig").splitlines()
        if not line.lstrip().startswith("#")
    )
    match = re.fullmatch(r"\s*BEFORE:(.*?)AFTER:(.*)", text, re.DOTALL)
    if not match:
        sys.exit("pad_filler.txt needs a BEFORE: line followed by an AFTER: line")
    return tuple(" ".join(part.split()) for part in match.groups())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true", help="overwrite pads that were edited by hand"
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    before, after = load_filler()
    filler = f"{before} {after}".strip()
    words = len(filler.split())
    if not words:
        sys.exit("pad_filler.txt is empty; write the BEFORE/AFTER filler first")
    if facts := new_facts("", filler):
        sys.exit(f"filler contains names/numbers, which would be new claims: {', '.join(facts)}")
    print(f"filler: {len(before.split())} words before + {len(after.split())} after = {words}")
    if not TARGET_WORDS[0] <= words <= TARGET_WORDS[1]:
        print(f"  WARN: target is ~100 words ({TARGET_WORDS[0]}-{TARGET_WORDS[1]})")

    records = json.loads((HERE / "records.json").read_text(encoding="utf-8"))
    written, skipped = 0, []
    for i, record in enumerate(records, 1):
        path = WORKBENCH / f"{i:02d}_{record['id']}" / "verbosity_pad.txt"
        padded = " ".join(part for part in (before, record["answer"], after) if part)
        current = read_mutant(path)
        if current not in (None, record["answer"], padded) and not args.force:
            skipped.append(path.parent.name)
            continue
        path.write_text(padded, encoding="utf-8", newline="\n")
        written += 1
    print(f"wrote {written} pad files")
    if skipped:
        print(
            f"skipped {len(skipped)} hand-edited pads (use --force to overwrite): "
            f"{', '.join(skipped)}"
        )


if __name__ == "__main__":
    main()
