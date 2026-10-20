# Metrics
MTLJ-35 · Ethan Goedelman · 2026-10-19 · **Status: proposed, pending team review**

How mtlj turns judge scores into results. One section per metric.

## Detection rate

The headline metric for degrading operators: **the share of degrading mutants whose score
drops by more than the judge's own run-to-run noise can explain.**

### Why the threshold is relative to noise

Judges are noisy. Score the same unchanged answer five times and you can get different scores:
in the spike (MTLJ-31), 7 of 20 originals moved across five runs, and one (record 17472)
ranged from 0.67 to 1.00. "Did the score drop?" therefore can't be answered with a fixed cutoff.
"Any drop" counts noise as a catch. A fixed drop like 0.1 is too strict for a steady judge
and too lenient for a noisy one, so detection rates from two judges wouldn't be comparable.
Instead, each run measures the judge's noise on the unmutated originals and asks whether the
mutant's drop is bigger than that noise would produce by chance.

### Definition

Computed separately for each judge, judge config and operator in a run. Every original and
every mutant is scored N times. Scores are on [0, 1] with higher = better (the judge adapter
normalizes them).

```
o_r1 … o_rN    scores of original r           m_r1 … m_rN    scores of its mutant
s_r²           sample variance of o_r1 … o_rN (n − 1 denominator)

σ    = √( Σ_r (N_r − 1)·s_r²  ÷  Σ_r (N_r − 1) )     noise floor
τ    = z · σ · √( 1/N_o + 1/N_m ),  z = 1.645           detection threshold (α = 0.05)
Δ_r  = mean(o_r) − mean(m_r)                            drop in mean score
detected_r   ⇔   Δ_r > τ + ε,  ε = 1e-9                floating-point tolerance
DR   = detected mutants ÷ testable mutants              detection rate
```

With the usual equal N, these reduce to σ = √(mean of s_r²) and τ = 1.645 · σ · √(2/N).

- **Noise floor σ** is the pooled within-original standard deviation: how far the judge's
  score moves when nothing changed. It pools every original in the run that has 2+ scores, so
  it describes this judge on this corpus sample. It's re-estimated every run, never hard-coded.
- **Threshold τ.** If the mutation made no difference, the mutant's scores come from the same
  distribution as the original's, and the difference of the two means has standard deviation
  σ · √(2/N). τ is that distribution's one-sided 95th percentile, so noise alone gets past it
  for about 5% of mutants. It's one-sided because a degrading operator predicts a drop. A
  higher mutant score is never a detection.
- **Tolerance ε** only absorbs floating-point rounding. It matters when σ = 0 (the judge gave
  each original the same score on every run), because then τ = 0. Without ε, a mutant whose
  scores average to the original's mean could still count as detected through a rounding
  error of about 1e-17. Real score steps are far larger (about 0.1), so ε never changes a
  genuine verdict.
- **Detection rate** is reported with a 95% Wilson score interval, which behaves at 0% and
  100% and with few mutants.

More samples shrink τ, because each mean is estimated more precisely:

| N | 2 | 3 | 5 | 10 |
|---|---|---|---|---|
| τ ÷ σ | 1.64 | 1.34 | 1.04 | 0.74 |

More samples do not help with a judge that never moves on a mutant. That is a real miss, and
the detection rate should count it.

### Testable mutants

Excluded from the denominator. The report counts each reason:

- **No change:** the operator returned `changed=False`.
- **No scores:** the original or the mutant has no successful scoring after retries.
- **No headroom:** mean(o_r) ≤ τ. Scores can't go below 0, so the mutant can't drop by more
  than τ, however good the judge.

### Reported with it

Detection rate, its 95% CI, detected ÷ testable, excluded counts by reason, σ, τ, N and α.
Run provenance records N and α so the result can be reproduced.

### Choices behind the definition

**Pooled across originals, not per original.** At N = 5, 13 of the spike's 20 originals gave
the same score all five times, so a per-original SD would set τ = 0 for them and count any
drop. For the rest, the SD would come from only 4 degrees of freedom. Pooling estimates the
same quantity (this judge's run-to-run noise) from all originals at once.

**Pooled variance, not the spike's mean of SDs.** Noise is concentrated in a few records (one
original, 17472, contributes 53% of the total variance). Averaging SDs then understates it:
0.028 against 0.054 here.

**The data backs this choice.** On the spike's default config, padding and reformatting should not
move the score, so the rule should fire on about 5% of them:

| Rule | τ | Swaps detected | Pad/reformat flagged |
|---|---|---|---|
| Any drop | 0 | 13/20 | 8/40 (20%) |
| Fixed drop of 0.1 | 0.100 | 8/20 | 1/40 (3%) |
| Per-original SD | 0 for 13 of 20 | 11/20 | 6/40 (15%) |
| Mean of SDs (spike's noise floor) | 0.029 | 13/20 | 6/40 (15%) |
| **Pooled SD (this definition)** | **0.056** | **12/20** | **3/40 (8%)** |

**α = 0.05, fixed.** This is the usual convention. It isn't tuned per judge, so detection rates
stay comparable across judges and runs.

**Normal approximation.** Scores are coarse (about 8 claims per answer, so steps of about 0.125),
so the 5% is approximate. Checking preserving operators against the same rule, as above, tests
it empirically. Reformat isn't a perfectly neutral change either: it alters how claims are
split.

### Worked example

Real spike data: DeepEval `FaithfulnessMetric` on gpt-4o-mini, default config, the 20
hand-written entity swaps, N = 5. To reproduce, run `python detection_rate.py` from `spike/`.

**1. Noise floor.** 13 originals scored identically all five times (s_r² = 0). The other seven:

| Record | Scores of the original | s_r |
|---|---|---|
| 12852 | 1.000, 1.000, 1.000, 1.000, 0.857 | 0.064 |
| 14352 | 1.000, 0.833, 1.000, 1.000, 0.833 | 0.091 |
| 14922 | 0.857, 0.857, 0.857, 0.857, 1.000 | 0.064 |
| 17388 | 0.929, 0.929, 0.857, 0.923, 0.929 | 0.031 |
| 17472 | 1.000, 1.000, 0.714, 0.667, 0.667 | 0.175 |
| 17490 | 1.000, 0.857, 0.857, 0.889, 1.000 | 0.074 |
| 17622 | 0.917, 0.875, 1.000, 0.833, 0.875 | 0.063 |

Σ s_r² = 0.0575 over 20 originals → mean 0.00288 → **σ = 0.0536**.

**2. Threshold.** τ = 1.645 × 0.0536 × √(2/5) = **0.0558**. A swap counts as detected only if
it lowers the mean score by more than about 0.056.

**3. Each mutant.** No original is near 0, so all 20 are testable. Numbering follows the spike's
appendix.

| # | Record | Swap | Original | Mutant | Δ | Detected |
|---|---|---|---|---|---|---|
| 01 | 13122 | oven time 5 → 2 min | 1.000 | 0.833 | +0.167 | yes |
| 02 | 15450 | 2 to 3 → 4 to 5 weeks | 1.000 | 0.833 | +0.167 | yes |
| 03 | 16980 | 192.168.1.1 → 192.168.0.0 | 1.000 | 0.857 | +0.143 | yes |
| 04 | 17622 | 1 cup → 3 cups of water | 0.900 | 0.800 | +0.100 | yes |
| 05 | 13302 | 2010 → 2020 | 1.000 | 1.000 | 0.000 | no |
| 06 | 17388 | two to three → four to five days | 0.913 | 0.843 | +0.070 | yes |
| 07 | 13560 | stand 10 → 2 min | 1.000 | 1.000 | 0.000 | no |
| 08 | 15204 | British → German goods | 0.857 | 0.857 | 0.000 | no |
| 09 | 14922 | "Scan Leftovers" → "Add Leftovers" | 0.886 | 0.829 | +0.057 | yes |
| 10 | 12660 | 99.8 → 97.8 F | 1.000 | 1.000 | 0.000 | no |
| 11 | 17148 | hold 30 → 90 seconds | 1.000 | 0.875 | +0.125 | yes |
| 12 | 12852 | oven 5 → 15 min | 0.971 | 0.880 | +0.091 | yes |
| 13 | 17490 | 3-6 → 6-8 inches | 0.921 | 0.927 | −0.006 | no |
| 14 | 17028 | six → four days a week | 1.000 | 0.864 | +0.136 | yes |
| 15 | 11868 | 1 part → 2 parts alcohol | 1.000 | 1.000 | 0.000 | no |
| 16 | 17592 | Bilbo → Frodo | 0.889 | 0.610 | +0.279 | yes |
| 17 | 14352 | boil 15 → 5 min | 0.933 | 0.900 | +0.033 | no |
| 18 | 17472 | copy to → delete from | 0.810 | 0.838 | −0.029 | no |
| 19 | 12018 | four → 5 types of bases | 1.000 | 0.833 | +0.167 | yes |
| 20 | 16080 | 1% → 3% | 1.000 | 0.750 | +0.250 | yes |

**4. Detection rate.** 12 / 20 = **60%**, 95% CI [39%, 78%].

What the example shows:

- **The threshold changes verdicts, not just numbers.** Swap 17 lowered the mean by 0.033, and the
  spike counted it as caught. But that record's original already swings between 0.833 and 1.000
  across runs, so a 0.033 drop is within its noise. Under this definition it's a miss.
- **Marginal cases exist.** Swap 09 clears τ by 0.001. One of its five mutant runs scored 0.714,
  and the other four scored 0.857, the original's usual score. A different draw of five runs
  could flip it.
- **Most misses are flat.** Seven of the eight misses had Δ ≤ 0: on average the mutant scored no
  lower than the original, and more samples can't change that. This matches the spike's finding.
- **The same rule rarely fires on preserving mutants.** Padding: 1 of 20. Reformatting: 2 of 20.
- **The penalize config gives a different result.** With `penalize_ambiguous_claims=True`: σ = 0.062,
  τ = 0.064, detection rate 11 / 20 = 55% [34%, 74%], with padding and reformatting flagged in
  5 of 40. Detection rate belongs to a judge *and* its config.

### What detection rate doesn't tell you

- **How far scores fell.** Report the mean Δ and effect size ([spike](spike-effect-size.md)) with it.
- **Why a mutant was missed.** The spike found most misses were "yes" → "borderline" verdict
  shifts that the score ignores. Record the verdicts separately.
- **Anything about preserving operators.** They ask the opposite question (does the score stay
  put?), which needs its own metric. That metric can reuse σ and τ.
- **Whether the mutant is valid.** The metric assumes every mutant is worse than its original. An
  invalid swap (a synonym, or a value the context still supports) counts as a miss against the
  judge. The spike's entity-swap operator rules exist to prevent this.

### Decisions

Agreed by the team on 2026-10-20:

- **Evaluations store verdict counts alongside the score.** Each scoring records the judge's
  yes / borderline / no claim verdict counts next to the float score, so the verdict shifts the
  score ignores (see "What detection rate doesn't tell you") are kept.
- **Default N is 5.** Every original and every mutant is scored 5 times per run for audits. N is
  still recorded in run provenance, so a run can override it and old runs stay reproducible.

### Code

`src/mtlj/stats/detection.py` implements this definition. `tests/stats/test_detection.py`
checks that it reproduces the worked example above for both configs. `spike/detection_rate.py`
is a standard-library-only script that prints the worked example from `spike/scores.csv`.
