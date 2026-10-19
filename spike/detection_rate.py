"""MTLJ-35: detection rate of the spike's entity swaps, as defined in docs/metrics.md.

Prints the numbers behind the worked example in docs/metrics.md for both judge configs:
the noise floor, the detection threshold, every swap's delta and whether it was detected,
the detection rate with its 95% CI, and how often the same rule fires on the two
mutation types that should not move the score.

Needs only the standard library. Run from spike/:
    python detection_rate.py
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist

HERE = Path(__file__).parent
ALPHA = 0.05
EPSILON = 1e-9  # floating-point tolerance on the detection rule; matters when tau is 0
DEGRADING = "entity_swap"
PRESERVING = ("verbosity_pad", "reformat")

Scores = dict[tuple[str, str], list[float]]


def load_scores() -> dict[str, Scores]:
    """{config: {(record_id, mutation_type): [score, ...]}} from scores.csv."""
    scores: dict[str, Scores] = defaultdict(lambda: defaultdict(list))
    with (HERE / "scores.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            scores[row["config"]][(row["record_id"], row["mutation_type"])].append(
                float(row["score"])
            )
    return scores


def noise_floor(originals: list[list[float]]) -> float:
    """Pooled within-original SD: the judge's run-to-run noise on unmutated answers."""
    pooled = [s for s in originals if len(s) > 1]
    df = sum(len(s) - 1 for s in pooled)
    return math.sqrt(sum((len(s) - 1) * statistics.variance(s) for s in pooled) / df)


def threshold(sigma: float, n_original: int, n_mutant: int, alpha: float = ALPHA) -> float:
    """Smallest drop in mean score that noise alone exceeds with probability below alpha."""
    return NormalDist().inv_cdf(1 - alpha) * sigma * math.sqrt(1 / n_original + 1 / n_mutant)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson score interval for a proportion k/n."""
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def detections(texts: Scores, mtype: str, sigma: float) -> list[tuple[str, float, float, bool]]:
    """(record_id, delta, tau, detected) for every testable mutant of one mutation type."""
    rows = []
    for (record_id, kind), mutant in sorted(texts.items()):
        if kind != mtype:
            continue
        original = texts[(record_id, "original")]
        tau = threshold(sigma, len(original), len(mutant))
        if statistics.fmean(original) <= tau:
            continue  # no headroom: the score cannot fall by more than tau
        delta = statistics.fmean(original) - statistics.fmean(mutant)
        rows.append((record_id, delta, tau, delta > tau + EPSILON))
    return rows


def report(config: str, texts: Scores) -> None:
    """Print the worked example for one judge config."""
    originals = {r: s for (r, kind), s in texts.items() if kind == "original"}
    sigma = noise_floor(list(originals.values()))
    nonzero = sum(statistics.stdev(s) > 0 for s in originals.values())
    print(f"\n=== config: {config}")
    print(f"originals: {len(originals)}, {nonzero} with any run-to-run variation")
    print(f"noise floor sigma = {sigma:.4f}")

    rows = detections(texts, DEGRADING, sigma)
    print(f"threshold tau = {rows[0][2]:.4f}  (alpha {ALPHA}, one-sided)")
    print(f"\n  record   orig    mutant   delta   detected   ({DEGRADING})")
    for record_id, delta, _, detected in rows:
        original = statistics.fmean(originals[record_id])
        print(
            f"  {record_id:<8}{original:>6.3f}{original - delta:>9.3f}{delta:>+8.3f}"
            f"   {'yes' if detected else 'no'}"
        )
    hits = sum(d for *_, d in rows)
    lo, hi = wilson(hits, len(rows))
    excluded = sum(kind == DEGRADING for _, kind in texts) - len(rows)
    print(
        f"\ndetection rate {hits}/{len(rows)} = {hits / len(rows):.0%}  "
        f"95% CI [{lo:.0%}, {hi:.0%}]  ({excluded} excluded for no headroom)"
    )
    for mtype in PRESERVING:
        fired = sum(d for *_, d in detections(texts, mtype, sigma))
        print(f"same rule on {mtype}: fires on {fired}/{len(originals)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    scores = load_scores()
    for config in sorted(scores):
        report(config, scores[config])


if __name__ == "__main__":
    main()
