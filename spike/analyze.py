"""MTLJ-31 spike: noise floor, paired deltas, effect sizes and implied N from scores.csv.

For each config separately:
    noise floor  = mean across originals of the within-text SD of their samples
    delta        = per record, mean(original) - mean(mutant); averaged across records
    effect size  = delta / noise floor
    implied N    = ceil(8 * (noise / delta)^2)

Only rows whose text_hash matches the current text in records.json / mutants.json
are used, so scores for a since-edited mutation never leak in.

Run from spike/ with the venv active:
    python analyze.py
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from run_spike import load_texts, text_hash

HERE = Path(__file__).parent
TYPES = ("original", "entity_swap", "verbosity_pad", "reformat")
VERDICT = (
    (2.0, "Strong. N of 3-5 plenty."),
    (0.8, "Workable. N ~ 8-15."),
    (0.3, "Marginal. N gets expensive."),
    (0.0, "Premise in trouble for that operator."),
)
# These should NOT move the score, so they are judged on whether the CI excludes zero,
# not on effect size (which ignores record-to-record spread).
PRESERVING = {"verbosity_pad", "reformat"}


def load_scores() -> tuple[dict, int]:
    """{config: {(record_id, mutation_type): [(score, verdicts), ...]}} for current texts only."""
    current = {
        (t["record"]["id"], t["mutation_type"]): text_hash(t["text"])
        for t in load_texts(set(TYPES))
    }
    scores: dict = defaultdict(lambda: defaultdict(list))
    stale = 0
    with (HERE / "scores.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["record_id"], row["mutation_type"])
            if current.get(key) != row["text_hash"]:
                stale += 1
                continue
            scores[row["config"]][key].append((float(row["score"]), row["verdicts"].split("|")))
    return scores, stale


def verdict_for(mtype: str, effect: float, lo: float, hi: float) -> str:
    if mtype in PRESERVING:
        if lo <= 0 <= hi:
            return "No significant shift (CI includes 0). Healthy."
        return "Judge shifts the score on this change (false positive)."
    return next(text for floor, text in VERDICT if effect > floor or floor == 0.0)


def t_crit(df: int) -> float:
    """Two-sided 95% Student-t critical value (Cornish-Fisher; within 0.01 for df >= 10)."""
    z = 1.959964
    return z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)


def ci95(values: list[float]) -> tuple[float, float]:
    """95% CI for the mean of the per-record paired deltas."""
    half = t_crit(len(values) - 1) * statistics.stdev(values) / math.sqrt(len(values))
    mean = statistics.fmean(values)
    return mean - half, mean + half


def analyse(config: str, texts: dict) -> None:
    by_type: dict = defaultdict(dict)
    for (record_id, mtype), rows in texts.items():
        by_type[mtype][record_id] = rows
    originals = by_type["original"]

    sds = [statistics.stdev(s for s, _ in rows) for rows in originals.values() if len(rows) > 1]
    noise = statistics.fmean(sds) if sds else float("nan")
    distinct = sorted({s for rows in originals.values() for s, _ in rows})
    samples = sorted({len(rows) for rows in texts.values()})

    print(f"\n{'=' * 78}\nconfig: {config}   (samples per text: {samples})")
    print(f"noise floor (mean within-text SD over {len(sds)} originals): {noise:.4f}")
    print(f"originals whose score varied across samples: {sum(sd > 0 for sd in sds)}/{len(sds)}")
    print(
        f"distinct score values across originals: {len(distinct)}  "
        f"{[round(d, 3) for d in distinct][:12]}{' ...' if len(distinct) > 12 else ''}"
    )
    all_original = [s for rows in originals.values() for s, _ in rows]
    print(f"mean original score: {statistics.fmean(all_original):.3f}")

    print(
        f"\n{'mutation type':<15}{'records':>8}{'delta':>9}{'95% CI':>19}"
        f"{'effect':>9}{'implied N':>11}{'  any NO':>9}   verdict"
    )
    for mtype in TYPES[1:]:
        paired = [r for r in by_type[mtype] if r in originals]
        if not paired:
            continue
        deltas = [
            statistics.fmean(s for s, _ in originals[r])
            - statistics.fmean(s for s, _ in by_type[mtype][r])
            for r in paired
        ]
        delta = statistics.fmean(deltas)
        lo, hi = ci95(deltas)
        rows = [v for r in paired for _, v in by_type[mtype][r]]
        any_no = sum("no" in v for v in rows) / len(rows)
        if math.isnan(noise):
            tail = (
                f"{'n/a':>9}{'n/a':>11}{any_no:>9.0%}   "
                "need 2+ samples per original for a noise floor"
            )
        elif noise == 0:
            tail = f"{'n/a':>9}{'n/a':>11}{any_no:>9.0%}   noise floor is 0: report raw delta"
        elif delta == 0:
            tail = f"{0:>9.2f}{'inf':>11}{any_no:>9.0%}   {verdict_for(mtype, 0.0, lo, hi)}"
        else:
            effect = delta / noise
            implied = math.ceil(8 * (noise / delta) ** 2)
            label = verdict_for(mtype, abs(effect), lo, hi)
            tail = f"{effect:>9.2f}{implied:>11}{any_no:>9.0%}   {label}"
        print(f"{mtype:<15}{len(paired):>8}{delta:>9.4f}{f'[{lo:+.3f}, {hi:+.3f}]':>19}{tail}")

    orig_rows = [v for rows in originals.values() for _, v in rows]
    orig_no = sum("no" in v for v in orig_rows) / len(orig_rows)
    print(f"{'(originals)':<15}{len(originals):>8}{'':>48}{orig_no:>9.0%}")
    print("  'any NO' = share of scorings where the judge ruled at least one claim 'no'")

    for mtype in TYPES[1:]:
        paired = [r for r in by_type[mtype] if r in originals]
        if not paired:
            continue
        print(f"\n  per-record {mtype}:  record   orig    mutant   delta")
        for r in paired:
            o = statistics.fmean(s for s, _ in originals[r])
            m = statistics.fmean(s for s, _ in by_type[mtype][r])
            print(f"  {'':<{len(mtype) + 13}}{r:<8}{o:>6.3f}{m:>9.3f}{o - m:>8.3f}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    scores, stale = load_scores()
    if stale:
        print(f"ignored {stale} rows scored against text that has since changed")
    for config in sorted(scores):
        analyse(config, scores[config])


if __name__ == "__main__":
    main()
