"""MTLJ-33: spurious sensitivity of the spike's preserving mutants, as defined in docs/metrics.md.

Prints the numbers behind the worked example in docs/metrics.md for both judge configs: the
noise floor, the two-sided threshold, every padded and reformatted answer's delta and whether
it was flagged, and the spurious sensitivity with its 95% CI and the binomial test against
alpha.

Needs only the standard library. Run from spike/:
    python spurious_sensitivity.py
"""

from __future__ import annotations

import math
import statistics
import sys

from detection_rate import ALPHA, EPSILON, PRESERVING, Scores, load_scores, noise_floor, wilson

Z_TWO_SIDED = statistics.NormalDist().inv_cdf(1 - ALPHA / 2)


def binomial_tail(k: int, n: int, p: float = ALPHA) -> float:
    """P(X >= k) for X ~ Binomial(n, p): the chance of k or more flags from noise alone."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def report(config: str, texts: Scores) -> None:
    """Print the worked example for one judge config."""
    originals = {r: s for (r, kind), s in texts.items() if kind == "original"}
    sigma = noise_floor(list(originals.values()))
    print(f"\n=== config: {config}")
    print(f"noise floor sigma = {sigma:.4f}")

    for mtype in PRESERVING:
        print(f"\n  record   orig    mutant   delta   flagged   ({mtype})")
        flags = []
        for (record_id, kind), mutant in sorted(texts.items()):
            if kind != mtype:
                continue
            original = originals[record_id]
            tau = Z_TWO_SIDED * sigma * math.sqrt(1 / len(original) + 1 / len(mutant))
            delta = statistics.fmean(original) - statistics.fmean(mutant)
            flag = "no" if abs(delta) <= tau + EPSILON else "down" if delta > 0 else "up"
            flags.append(flag)
            print(
                f"  {record_id:<8}{statistics.fmean(original):>6.3f}"
                f"{statistics.fmean(mutant):>9.3f}{delta:>+8.3f}   {flag}"
            )
        hits = len(flags) - flags.count("no")
        lo, hi = wilson(hits, len(flags))
        print(f"threshold tau = {tau:.4f}  (alpha {ALPHA}, two-sided)")
        print(
            f"spurious sensitivity {hits}/{len(flags)} = {hits / len(flags):.0%}  "
            f"95% CI [{lo:.0%}, {hi:.0%}]  ({flags.count('down')} down, {flags.count('up')} up)"
            f"  binomial p = {binomial_tail(hits, len(flags)):.4f} against {ALPHA}"
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    scores = load_scores()
    for config in sorted(scores):
        report(config, scores[config])


if __name__ == "__main__":
    main()
