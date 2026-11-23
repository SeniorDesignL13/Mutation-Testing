# Pilot audits: sizing the full study
MTLJ-89 · 2026-11-22

## Question
How long will the full audit take and cost, is N = 5 the right sample count, and is anything
broken that we would rather find now than after hours of compute?

**Short answer.** One full corpus-v1 audit of one judge configuration is **34,885 scorings**.
Measured live (20 records, 785 scorings per configuration), that is about **21 hours at 4
concurrent calls or 11 hours at 8, and $21**, per configuration. DeepEval has two
configurations on main, so the study is twice that: about **42 hours at 4 concurrent, or 21 at 8,
and $42**. The live pilots confirmed the offline estimate of time (replay 20.5 h, live 21.5 h) and
found cost half the offline ceiling ($0.0006 a scoring, not $0.0013). Going from 4 to 8 concurrent
calls halved the wall clock with no errors or retries. N = 5 still looks right. **Do not start the
full run yet**: the entity-swap operator makes invalid mutants, `numeric_perturbation` loses two
thirds of its intensities, the runner can't resume or reuse paid-for scores, so one failure at
hour 15 costs the whole run, and about 0.5% of live scorings fail on a DeepEval verdict-count
check.

## What was run

| Part | Data | Status |
|---|---|---|
| A. Replay of the recorded gold-set pilot through the real engine | **Recorded** (`tests/fixtures/judge/deepeval-faithfulness.jsonl`, 765 scorings, gpt-4o-mini, DeepEval 4.2.8, default config) | Done |
| A. Planned scorings for corpus-v1 | **Generated locally** (no judge, no network) | Done |
| B. Live pilots, `--dataset corpus-v1 --limit 20`, N = 5, seed 0, 4 concurrent, both DeepEval configs | **Live** (gpt-4o-mini) | Done, 2 × 785 scorings. Total spend **$1.05** (cap $3) |
| B. Concurrency 8, 4 records, default config | **Live** | Done, 165 scorings |
| Ragas pilot | Live | Pending MTLJ-65 |

Part A is reproducible with no key:

```bash
uv run python scripts/pilot_analysis.py all
```

`scripts/pilot_analysis.py` writes `runs/pilot-gold-replay/` (git-ignored) and the summary data
in [`docs/pilot/`](pilot/): `corpus_v1_generation.json`, `replay_run.json`, `summary.json`,
`per_text.csv`. Part B needs a key and spends money (see "Part B: live pilots" for the commands);
its summary is `live_summary.json`, written by `scripts/pilot_analysis.py live`.

## Method

**Replay.** The 20 gold-set records, their 133 default-battery mutants and 5 samples each are
scored by `run_judge_sync` with a `FixtureStore` as the cache, then `build_results` and
`write_results`, exactly as `mtlj audit run` does. The judge is never called. The result is a
normal run directory: 765 scorings, 0 failed, and a `results.json` (noise floor 0.030). The
script also checks that today's operators still make the same 133 mutants as the saved ones
(they do), which also gives the 67 skipped mutants the fixtures don't save.

**Latency.** Each replayed evaluation keeps the `latency_ms` of the call that was recorded. That
is the only latency data offline, so everything below is only as good as one recording session
(20 records, one evening, 4 concurrent calls).

**Corpus-v1 counts.** All 919 records go through the generator with the default battery, and
separately with `numeric_perturbation` at 0.2 / 0.6 / 1.0. Generation is deterministic (seed 0)
and took 149 s on 4 processes. A scoring is one judge call; every text is scored N = 5 times.

**Cost, offline,** is an estimate. The README says 115 scorings cost under $0.15 with
gpt-4o-mini, so the offline figure is **at most $0.0013 per scoring**, as a ceiling. Tokens
aren't recorded in the fixtures. **Cost, live,** is measured: `scripts/pilot_live.py` runs the same
`run_audit` as the CLI and adds up DeepEval's `evaluation_cost` for every scoring (priced from
the response's token usage). It tallies tokens where DeepEval turns usage into a cost. Both are
DeepEval's own pricing for gpt-4o-mini, not an OpenAI invoice.

## Results

### Planned scorings, corpus-v1 (919 records, N = 5)

| Operator (default battery) | Intensities | Mutants | Records with one |
|---|---|---|---|
| `verbosity_padding` | 0.125 / 0.25 / 0.5 / 1.0 | 3,676 (919 each) | 919 |
| `reformat_to_bullets` | 1.0 | 919 | 919 |
| `reformat_to_markdown` | 1.0 | 919 | 919 |
| `reformat_to_prose` | 1.0 | 6 | 6 |
| `entity_swap` | 0.2 / 0.6 / 1.0 | 538 (364 / 151 / 23) | 364 |
| **Total** | | **6,058** | |

| | Texts | Scorings (× 5) |
|---|---|---|
| Originals | 919 | 4,595 |
| Default-battery mutants | 6,058 | 30,290 |
| **Default battery** | 6,977 | **34,885** |
| `numeric_perturbation` added (399 mutants) | +399 | +1,995 |
| **Default battery + `numeric_perturbation`** | 7,376 | **36,880** |

Skipped before scoring, as the generator reports them:

| Operator | no_change | repeated_dose |
|---|---|---|
| `entity_swap` 0.2 / 0.6 / 1.0 | 555 / 555 / 555 | 0 / 213 / 341 |
| `reformat_to_prose` | 913 | 0 |
| `numeric_perturbation` 0.2 / 0.6 / 1.0 | 520 / 520 / 520 | 0 / **399** / **399** |

### Runtime of the recorded run

Latency per scoring, in seconds, from the 765 recorded calls:

| | n | Mean | Median | p90 | p99 | Max |
|---|---|---|---|---|---|---|
| All | 765 | 8.46 | 8.30 | 10.89 | 13.87 | 16.86 |
| Originals | 100 | 8.41 | 8.26 | 10.70 | 14.42 | 16.37 |
| Mutants | 665 | 8.47 | 8.30 | 10.95 | 13.47 | 16.86 |
| `verbosity_padding` | 400 | 8.48 | 8.25 | 11.16 | 13.67 | 15.53 |
| `reformat_to_bullets` | 100 | 8.42 | 8.36 | 11.21 | 13.35 | 16.86 |
| `reformat_to_markdown` | 100 | 8.26 | 8.10 | 10.62 | 13.25 | 14.23 |
| `entity_swap` | 65 | 8.79 | 8.65 | 10.52 | 11.60 | 11.78 |

The operator makes little difference: the largest padding (400 words) is within half a second
of the originals. What matters is the
number of claims, which the judge has to check one by one (correlation of latency with
`n_claims` 0.64; the slowest record, 17622, has 15.6 claims and 12.9 s). Corpus answers have the
same median length as the gold set's, 99 words, so the gold-set mean is a fair planning figure.
Nothing in the recording was retried (`attempts` is 1 everywhere; there are no errors).

Two earlier figures disagree with that latency, so the extrapolation is shown on both:

| Source | Seconds per scoring, wall clock at 4 concurrent |
|---|---|
| This recording (mean 8.46 s ÷ 4) | 2.1 |
| README ("115 scorings, about 4 minutes") | 2.1 |
| The spike ("400 scorings took 6.7 minutes") | 1.0 |

The recording and the README agree, and are used below. If the spike's speed is what OpenAI
gives on the day, the times halve.

### Extrapolation for the full study

Time = Σ (planned scorings × mean latency of that operator) ÷ concurrency. It assumes calls
overlap perfectly. The live concurrency-8 run supports that (Part B: 165 scorings in 182 s, no
errors or retries), though only over 3 minutes. The **replay** rows use the recorded latency,
`numeric_perturbation` takes `entity_swap`'s latency, and cost is the README ceiling of $0.0013 a
scoring. The **live** rows use the mean latency and measured cost of each live pilot (default
config 8.89 s and $0.00060; penalized 8.52 s and $0.00060).

| Setup | Scorings | Hours, 4 concurrent | Hours, 8 concurrent | Cost |
|---|---|---|---|---|
| **Live**: default config, default battery | 34,885 | 21.5 | 10.8 | $21.0 |
| **Live**: penalized config, default battery | 34,885 | 20.6 | 10.3 | $21.0 |
| **Live: both configs, default battery** | 69,770 | **42.2** | **21.1** | **$42.0** |
| **Live: both configs, + `numeric_perturbation`** | 73,760 | 44.6 | 22.3 | $44.4 |
| Replay: one config, default battery | 34,885 | 20.5 | 10.2 | $45.5 (ceiling) |
| Replay: one config, + `numeric_perturbation` | 36,880 | 21.7 | 10.9 | $48.1 (ceiling) |
| Replay: both configs, default battery | 69,770 | 41.0 | 20.5 | $91 (ceiling) |
| One config at the spike's speed (1.0 s a scoring at 4 concurrent) | 34,885 | 9.7 | 4.9 | |

The live time is 5% above the replay's (mean latency 8.89 s against 8.46 s), so the offline
estimate held. The live cost is 46% of the ceiling. The two DeepEval configs cost and take the
same (the penalized pilot cost $0.4721 against $0.4727, and ran 4% faster). A Ragas judge is not
included.

### Disk and memory

| | Measured |
|---|---|
| Run directory, gold replay | 781 KB for 765 scorings, **about 1.0 MB per 1,000 scorings** |
| of which `evaluations.jsonl` | 356 KB, 466 bytes a scoring (455 KB per 1,000) |
| Full run, default battery | about 35 MB |
| Peak memory of the replay process | 283 MB working set (mostly imports: spaCy, SciPy, DeepEval) |

A live run adds the OpenAI client and 4 to 8 requests in flight, which wasn't measured.
Nothing here limits the full run: `JsonlSink` writes each scoring as it arrives, and
`build_results` holds the 35,000 evaluations in memory at the end, about 16 MB of JSON.

### Replay results (gold set, default config, N = 5)

Noise floor σ = 0.030 from 20 originals. Corrected across 8 tests.

| Operator | Rate | Testable | Detail |
|---|---|---|---|
| `entity_swap` (detection) | 46.2% [23.2%, 70.9%] | 13 | 6 detected. 0.2: 4/10, 0.6: 2/3, 1.0: 0 testable |
| `reformat_to_bullets` (spurious) | 25.0% [11.2%, 46.9%] | 20 | 5 flagged (3 penalized, 2 rewarded), above chance |
| `reformat_to_markdown` | 35.0% [18.1%, 56.7%] | 20 | 7 flagged (4 / 3), above chance |
| `reformat_to_prose` | n/a | 0 | all 20 no_change |
| `verbosity_padding` | 18.8% [11.7%, 28.7%] | 80 | 15 flagged (7 / 8). By intensity 10%, 20%, 25%, 20% |

The spike found 12 of 20 hand-written swaps detected, and no significant shift for padding or
bullets. Here, with a different noise floor, bullets, markdown and padding are all flagged above
the 5% that noise gives. See Anomalies, 7.

### Sample count: is N = 5 right?

**Noise floor.** σ = 0.0296 (pooled within-original SD, N = 5). The spike's canonical figure was
0.054 on the same 20 records, so the same judge's noise moved by almost half between two
recordings. Record 17472, 53% of the spike's variance, scored 1.0 on all five samples this time.
σ is estimated in every run and never fixed, as it should be, but a σ from one session is not a
property of the judge.

**Saturation.** The judge is nearly deterministic here:

| | Count |
|---|---|
| Originals with the same score on all 5 samples | **15 of 20** (13 of them at exactly 1.0) |
| Originals that vary | 5 (record 13122 alone is 32% of the variance) |
| Mutants with the same score on all 5 samples | 100 of 133 (75%) |
| Originals with mean ≥ 0.95 | 15 of 20 |
| Originals excluded for no headroom | 0 |

σ therefore rests on five records. It is much better determined with 919 originals, where
about a quarter will vary.

**Threshold τ at σ = 0.0296.** τ = z · σ · √(2/N), z = 1.645 for detection and 1.960 for
spurious sensitivity (two-sided).

| N | 2 | 3 | 5 | 10 |
|---|---|---|---|---|
| τ ÷ σ, detection | 1.64 | 1.34 | 1.04 | 0.74 |
| τ, detection | 0.049 | 0.040 | 0.031 | 0.022 |
| τ, spurious sensitivity | 0.058 | 0.047 | 0.037 | 0.026 |

One claim flipping in one of eight shifts a score by 0.125, so one disagreeing sample moves the
mean by 0.0625 at N = 2 (above τ: one flip detects), 0.042 at N = 3 (barely above), and 0.025 at
N = 5 (below: it takes two). At N = 5 one stray sample can't make a verdict.

**What smaller N would have changed.** The 5 recorded samples were re-cut into every subset of
2, 3 and 4 (10, 10 and 5 subsets), the same subset used on every text. Mean over subsets, and
the range for the operators with few mutants:

| N | σ | `entity_swap` hits (of 13) | `reformat_to_bullets` (of 20) | `reformat_to_markdown` | `verbosity_padding` (of 80) | Verdicts equal to N = 5's |
|---|---|---|---|---|---|---|
| 2 | 0.028 | 4.8 (23%–46% of testable) | 4.1 | 5.1 | 12.3 | 85% to 89% |
| 3 | 0.029 | 5.5 (31%–54%) | 4.2 | 5.5 | 13.2 | 87% to 92% |
| 4 | 0.030 | 6.0 (38%–54%) | 4.2 | 6.0 | 13.0 | 92% to 95% |
| 5 | 0.030 | 6 | 5 | 7 | 15 | 100% |

Fewer samples find slightly fewer of everything, in both directions, because τ is larger. No
rate moves by more than the interval around it (padding 15% → 19%, swaps 37% → 46%). At N = 3,
about 1 mutant in 10 gets a different verdict from N = 5's. These are near-threshold cases, and
nothing in the data says which N is "right" for them. N = 10 can't be tested: only 5 samples
exist, and the table shows τ would fall to 0.74σ while 75% of texts already repeat themselves.

**Verdict: keep N = 5.** The spike said 3 to 5 and the 2026-10-20 decision chose 5. The data
doesn't overturn that and doesn't strongly defend it either:

- N = 3 would have cut the run by 40% (about 12 of the 20 hours) and changed the headline rates
  by a few points, but it flips about 10% of per-mutant verdicts, and the per-mutant verdict is
  what the metrics are built on.
- N = 2 is too coarse: one sample out of two can decide a verdict.
- N = 10 doubles the cost for a smaller τ that mostly buys sensitivity to one-sample blips.
- What would save compute without changing τ is not fewer samples but reuse (Anomalies, 5).
  Scoring 2 samples and adding 3 only when they differ would cut about 45% on this data, but it
  makes N unequal and biases σ low, so it would need a new decision in
  [metrics.md](metrics.md). Not recommended for the first full run.

If the budget forces a cut, N = 3 is the fallback. The live pilots support keeping N = 5: on fresh records the judge is noisier than in the gold set (σ 0.085 and 0.072 against 0.030, 45% and 65% of originals varying against 25%), so fewer samples lose more, and at $0.0006 a scoring the extra samples cost about $8 per configuration over N = 3.

## Anomalies

Ordered by how much they would waste. The first three should be settled before the full run.

### 1. Entity swap makes invalid mutants (high)

The spike's known cases are still there, and they aren't rare. Of the 13 entity-swap mutants in
the gold set, 7 contain a swap that is not a same-type contradiction, 3 more are doubtful, and 3
are clean (author's reading of each swap against its sentence):

| Record | Swap | Reading | Detected |
|---|---|---|---|
| 11868 | LCD → Windex | doubtful (a technology term, not a name) | no |
| 12018 | "Deoxyribonucleic" → James | invalid (a word fragment tagged PERSON) | no |
| 13122 | Pat → Thomas | invalid (the verb "Pat the salmon dry") | yes, 2 of 5 samples |
| 13302 | 2010 → 2016 | **clean** | no |
| 13560 | Worcestershire → James | invalid (a sauce) | yes, Δ 0.20 |
| 14922 | uTorrent → "Scan Leftovers" | invalid (a menu label, not a program) | no |
| 15204 @0.2 | 1765 → 1760 | **clean** | no (mutant scored higher) |
| 15204 @0.6 | 1765 → 1764, Parliament → the Red Coats | **clean** | no |
| 16980 | Linksys → "Wireless" | doubtful (one of 4 mentions of a brand) | no |
| 17472 @0.2 | "Folder Tasks" → Vista | invalid (a menu label) | yes, 1 of 5 samples |
| 17472 @0.6 | + Windows → Vista | doubtful | yes |
| 17592 @0.2 | Bilbo → "Alder Partners" | invalid (a person replaced by a firm) | yes |
| 17592 @0.6 | Hobbit, Bilbo → "Harbor Analytics"; Smaug → Rome | invalid | yes |

**Evidence.** The 6 detections are all from invalid or doubtful swaps; the 3 clean swaps (a
tiny sample) were all missed. So the 46% detection rate is not a measure of how well the judge
catches wrong facts. Corpus-wide the pattern repeats: 833 substitutions in 538 mutants, 202
tagged PERSON, and the most-swapped tokens include "Bake" (8), "Select" (7), "Download" (6),
"Delete" (3), "Click Options" (3), "Pat" (4), "Worcestershire" (4) and "Fahrenheit" (10). Those
alone are 45 substitutions (5% of 833), and they are only among the top 25 tokens. 304 of the
833 replacements come from the generic `pool` of names and companies rather than from the
context.

**Also.** Only 364 of 919 records (40%) can be swapped at all; at least 311 of the other 555 have no
entity for the operator to consider. Intensity 1.0 yields 23 mutants and no testable result in the gold set, so it adds
almost nothing. This is the weakest part of the study and the part the project exists to measure.

**Recommendation.** Treat entity swap as not ready. Before the full run: tighten the operator
(skip sentence-initial verbs and imperatives, require the original and replacement to share a
type and not be taken from the generic pool, skip tokens that aren't names), then have a person
check 50 random corpus mutants. The gold set's 60 hand-written swaps remain the validation target
MTLJ-60 named. Intensity 1.0 (23 mutants) can go. This is for the
operator's ticket; no operator code was changed here.

### 2. `numeric_perturbation` loses two thirds of its intensities (high, for adding it)

`generate_mutants` drops a mutant whose "dose" (number of substitutions) matches a lower
intensity's. `numeric_perturbation` always changes exactly one number, so its dose is always 1
and its intensity means *how far*, not *how many*. At corpus-v1, 0.2 keeps 399 mutants and **all
399 at 0.6 and all 399 at 1.0 are dropped as `repeated_dose`**. The dose-response curve, the
point of intensity, can't exist for it.

Reproduction (first 60 records; 30 get a number swapped):

```python
from mtlj.data.ragtruth import load_corpus_v1
from mtlj.engine.generator import BatteryItem, generate_mutants
from mtlj.operators.registry import get_operator

records = load_corpus_v1().records[:60]
item = [BatteryItem(get_operator("numeric_perturbation"), (0.2, 0.6, 1.0))]
generate_mutants(records, item).mutants  # 30, all at 0.2
generate_mutants(records, item, drop_repeated_doses=False).mutants  # 90
```

Without the filter, 22 of the 30 records get three different texts at the three intensities
(the other 8 repeat a text at two of the intensities). `audit run` doesn't
expose `drop_repeated_doses`. **Recommendation.** Make `_dose` return `None` for operators whose
intensity isn't a count (or let an operator say so), then re-count. Adding it is still about
2,000 scorings at one intensity or about 6,000 at three, 6 to 17% on top of the default battery.

### 3. No resume, no cache, no rate limit in `audit run` (high for a 10 to 40 hour run)

- `run_audit` doesn't take a cache, so `mtlj audit run` never uses the response cache that the
  runner supports (`cache=` in `run_judge_sync`). Re-running after fixing entity swap would pay
  for every original and every unchanged padding and reformat mutant again: about 32,000 scorings
  already paid for, about $19 live and 19 hours at 4 concurrent (10 at 8).
- Resuming a run (MTLJ-91) and rate limiting (MTLJ-96) are listed in `runner.py` as not built.
  A run that aborts at hour 15 writes `results.json` with status `aborted` and no rates, and the
  next run starts at zero. The retry logic handles a 429, but at 8 concurrent calls nothing keeps
  the run under the account's limit.

**Recommendation.** Wire `ResponseCache` into `run_audit` and the CLI (the cache and its tests
exist). With it, resume is free: the same command again pays only for what is missing. Do this
before the full run, not after. Use 4 concurrent calls until a live pilot has shown no 429s at 8.

### 4. Verdict counts don't always add up to `n_claims` (low)

In 43 of 765 scorings (5.6%) the yes + borderline + no counts are fewer than `n_claims`: 1 fewer
in 37 of them, up to 6 fewer. They are concentrated in long, padded answers (29 of 400 padded
scorings) and in 8 records, 27 of them in three (17622, 17388, 17592). The score always equals (yes + borderline) ÷ verdicts, never ÷
`n_claims`, so no score is wrong. The consequence is for analysis: `n_claims` isn't the
denominator, and a number of verdicts should not be read off it. Worth a line in
[results.md](results.md).

**Live, the opposite case fails.** In the live pilots **7 of 1,570 scorings (0.45%)** failed
with a permanent `ValueError: verdict_counts must not sum to more than n_claims` (2 of 785 for the
default config, 5 of 785 for penalized): DeepEval returned more verdicts than claims. All 7 are
mutants with long answers (4 padded, 3 bulleted); the same mutant, `mut_e5767dc337e04ac5`,
failed in both configs, on different samples. Failed scorings are never recorded as fixtures,
which is why the replay can't show them. They aren't retried (permanent), so each leaves its
text with 4 of 5 scores, which the metrics tolerate. At corpus scale that is about 160 lost
scorings per config, and no run is blocked by them. **Recommendation.** Not a blocker, but
decide whether the adapter should keep such a scoring (with the verdict count capped) instead of
failing it, and list the failures in the study's write-up.

### 5. The runtime is now measured (resolved, one open question)

The offline latency held up live: 8.89 s mean (default) and 8.52 s (penalized) against 8.46 s in
the replay, and the wall clock followed concurrency (Part B). No scoring needed a retry, at 4
or 8 concurrent calls. The open question is a longer run at 8: the concurrency-8 run was 3
minutes and 165 scorings, and the OpenAI client retries a 429 by itself and shows it only as
extra latency, which was not seen (max 12.9 s). **Recommendation.** Use 8 concurrent calls, and
watch the first hour of the full run for retries and latency.

### 6. Other numbers that look odd and aren't

- **No errors, no retries, no `no_scores` or `no_headroom` exclusions**, in the 765 replayed scorings. Live there were 7 failures in 1,735 (Anomaly 4), and still no retries.
- **`reformat_to_prose` makes 6 mutants** in 919 records (913 no_change): the corpus is
  nearly all prose, as docs/operators.md says. It adds nothing to the study but costs 30 scorings.
- **Saturation at 1.0** (13 of 20 originals) is real, not a bug: those are faithful answers, and
  the judge says so every time. They carry no noise information, and a mutant of one can only
  be penalized, never rewarded.
- **Many borderline verdicts hide behind a 1.0:** 43 of the 76 original scorings that scored
  1.0 contain at least one `borderline` verdict, which the default config counts as faithful. A
  swap that turns "yes" into "borderline" is invisible to the default score, as the spike
  found. Verdict counts are stored, but no metric reads them yet.
- **Replacement character in the data:** the gold-set text "Bilbo�s" in record 17592 is a bad
  character in RAGTruth. It matters little (no corpus-v1 answer contains one:
  `answers_with_replacement_char` is 0), but the context passages weren't checked.

### 7. Spurious sensitivity is above chance, and not because of noise (for the write-up)

Bullets 25%, markdown 35%, padding 19% against 5%. The rate rises, not falls, with N (padding
15.4% at N = 2, 18.8% at N = 5), so it isn't τ being too small: these are consistent shifts,
which fits the spike's note that bullets change how claims are cut up. At 75% of texts constant
across samples, a different constant is a real reaction. Expect "the judge reacts to reformatting"
as a headline in the full study, and don't read it as a pilot failure. The spike's 2/20 and 3/20
came from a larger σ (0.054, τ = 0.066, against τ = 0.037 here). **The live pilot shows how
much this depends on σ**: with σ = 0.085 the live default config flagged bullets 10%, markdown 0%
and padding 6% (Part B), close to the 5% of chance. The judge's reaction to formatting is real
but small enough that a σ estimate decides whether it is called significant. Report both σ and
the rate.

## Part B: live pilots

**Live data**, gpt-4o-mini, DeepEval 4.2.8, `--dataset corpus-v1 --limit 20`, default battery,
N = 5, seed 0. `--limit 20` is the first 20 records by id; one of them, 11868, is in the gold
set. Each run planned 20 originals and 137 mutants, **785 scorings**. They ran one after the
other, through `scripts/pilot_live.py` (same `run_audit` as the CLI, plus cost):

```bash
uv run python scripts/pilot_live.py --judge deepeval-faithfulness --limit 20 \
    --output runs/pilot-default --expect-scorings 785 --max-cost 1.3
uv run python scripts/pilot_live.py --judge deepeval-faithfulness-penalized --limit 20 \
    --output runs/pilot-penalized --expect-scorings 785 --max-cost 1.3
uv run python scripts/pilot_live.py --judge deepeval-faithfulness --limit 4 --concurrency 8 \
    --output runs/pilot-c8 --max-cost 0.4
uv run python scripts/pilot_analysis.py live
```

Total spend **$1.05** against the $3 cap ($0.0096 for a 1-record trial, $0.4727, $0.4721 and
$0.0991 for the three runs), by DeepEval's pricing.

### Measured, next to the replay

| | Live default | Live penalized | Live default, 8 concurrent, 4 records | Replay (gold set) |
|---|---|---|---|---|
| Scorings | 785 | 785 | 165 | 765 |
| Concurrency | 4 | 4 | 8 | 4 (recorded) |
| Wall clock | 1,752 s (29.2 min) | 1,679 s (28.0 min) | 182 s | n/a |
| Wall clock per scoring | 2.23 s | 2.14 s | 1.11 s | 2.1 s (README, 4 min per 115) |
| Latency, mean | 8.89 s | 8.52 s | 8.62 s | 8.46 s |
| Latency, median | 8.56 s | 8.20 s | 8.33 s | 8.30 s |
| Latency, p90 | 11.30 s | 10.66 s | 10.73 s | 10.89 s |
| Latency, max | 19.07 s | 15.26 s | 12.88 s | 16.86 s |
| Failed scorings | 2 | 5 | 0 | 0 |
| Scorings retried | 0 | 0 | 0 | 0 |
| 429s seen | 0 | 0 | 0 | n/a |
| Cost per scoring | $0.00060 | $0.00060 | $0.00060 | $0.0013 (ceiling) |
| Cost, whole run | $0.4727 | $0.4721 | $0.0991 | n/a |
| LLM calls per scoring | 3.0 | 3.0 | 3.0 | n/a |
| Tokens per scoring, in / out | 1,959 / 514 | 1,959 / 512 | 1,923 / 521 | n/a |
| σ | 0.085 | 0.072 | 0.036 | 0.030 |
| Originals scored identically on all 5 samples | 11 of 20 (10 at 1.0) | 7 of 20 (3 at 1.0) | 2 of 4 | 15 of 20 (13 at 1.0) |
| Mutants scored identically on all 5 samples | 85 of 137 (62%) | 50 of 137 (36%) | 17 of 29 | 100 of 133 (75%) |
| Mean score of the originals | 0.929 | 0.778 | 0.940 | 0.970 |

Concurrency 8 halved the wall clock per scoring (2.23 s to 1.11 s) at an unchanged mean latency.
Wall clock per scoring is mean latency divided by concurrency in all three runs (2.22, 2.13,
1.08), so the extrapolation formula holds. Retries and 429s: no scoring was retried in the run
summary, and none of the 1,735 scorings failed with a rate-limit or timeout error. The OpenAI client
retries a 429 on its own (two times by default) and that doesn't appear as a retry: it would
show as latency, and the tail is no longer than the replay's. So "no 429s" means none were
visible, not that none occurred. Cost and tokens are the totals DeepEval reports over every LLM
call of the scoring (3 calls: claims, truths, verdicts), including the 7 scorings that later
failed. Peak memory and run directory size were not measured live.

The cost is 46% of the README ceiling ($0.0013), which was only "under 15 cents for 115
scorings". The full study at this price is about $21 per configuration.

### Headline results, live and replay

Detection rate (`entity_swap`) and spurious sensitivity (the rest), as hits / testable.

| Operator | Live default (σ 0.085) | Live penalized (σ 0.072) | Replay default (σ 0.030) |
|---|---|---|---|
| `entity_swap` | 62.5% (10/16) | 53.3% (8/15) | 46.2% (6/13) |
| `reformat_to_bullets` | 10.0% (2/20) | 20.0% (4/20) | 25.0% (5/20) |
| `reformat_to_markdown` | 0.0% (0/20) | 20.0% (4/20) | 35.0% (7/20) |
| `verbosity_padding` | 6.2% (5/80) | 26.3% (21/80) | 18.8% (15/80) |
| `reformat_to_prose` | 0/1 testable | 0/1 testable | 0 testable |

Read these as a check of the pipeline, not as findings: 20 records, the entity-swap mutants are
the ones Anomaly 1 describes, and the intervals are wide (the Wilson interval for 2 of 20 is
2.8% to 30.1%).

### What the live pilots add

- **σ is not a property of the judge.** Three runs of one judge gave 0.085, 0.036 and (replay)
  0.030, on different 4 to 20 records, and the penalized config gave 0.072. It is re-estimated
  in every run, so the study is safe, but compare rates only with their σ.
- **Saturation is lower on fresh records.** 55% of live originals are constant across samples
  (11 of 20, default) against 75% in the gold set, and only 35% under the penalized config. The
  gold set was picked for answers with several sentences and an entity, and the judge may be
  steadier on them; or the replay's 5 samples were simply quiet. The full study will have more
  noise than the replay suggests, which favours N = 5.
- **The penalized config is noisier and harsher**: original mean 0.78 against 0.93, more
  variation per text, and it flags padding 26% of the time against 6%. The spike's
  "default as baseline, penalized as second view" holds.
- **Entity swap detection is higher live (62% and 53% against 46%)**, on 15 to 16 mutants, with
  the same kind of swap Anomaly 1 describes. With these counts the difference is within
  noise, and it is no evidence the operator improved.

The `live` stage of `scripts/pilot_analysis.py` reads `runs/pilot-*` and writes
`docs/pilot/live_summary.json`. The run directories are git-ignored and `live_summary.json`
is the record kept. Cost and token counts per scoring are in each run's `cost.json`.

## Ragas pilot: pending MTLJ-65

Not started. When the adapter lands, repeat the corpus-v1 `--limit 20` run for it, record its
latency and cost per scoring (Ragas makes different calls from DeepEval, so neither carries
over), and add its rows to the extrapolation table.

## Recommendation: no-go for the full run today, go once two things are done

The pipeline works end to end live: 1,735 scorings, 7 failures, no retries, and a cost of $0.0006
a scoring. The study is about **$42 and 21 hours at 8 concurrent calls (42 hours at 4)** for both
DeepEval configs. The live pilots did what they were for: the time estimate held, the cost came in
at half the ceiling, and 8 concurrent calls worked. They changed one thing in the list:
the live pilot is done, so it is off the to-do list. But a full run today would spend that on a
degrading operator that mostly makes invalid mutants, and, if it fails part way, would start
again from zero.

1. **Fix or filter entity swap** and have a person check 50 mutants (Anomaly 1).
2. **Wire the response cache into `audit run`** so a stopped or repeated run only pays for
   what's missing (Anomaly 3). Cost is no longer the argument for this ($21 a config); the
   hours are.

Do these and the full run (default battery, N = 5, seed 0, 8 concurrent, both DeepEval
configs, about 21 hours and $42) can go ahead, watching the first hour for retries. Decide
about the 0.5% verdict-count failures (Anomaly 4) and add `numeric_perturbation` only after its
intensity handling is fixed (Anomaly 2). Run Ragas when MTLJ-65 is merged.

## Limitations

- Offline latency and σ come from one recording of 20 records. The live runs add two 785-scoring
  pilots and one 165-scoring run, on the first 20 (and 4) records by id, not a random sample.
- Cost is DeepEval's pricing of the token usage OpenAI reports, not an invoice.
- The 8-concurrent run lasted 3 minutes. A rate limit that only bites after tens of thousands
  of calls would not show.
- The invalid-swap reading is one person's, on 13 mutants, and has no second reader.
- The extrapolation assumes no throttling, no retries, and the same latency for
  `numeric_perturbation` and the penalized config as for the recorded ones.
- Subsampling re-cuts the same 5 samples, so the N = 2, 3 and 4 results are not independent of
  each other or of N = 5.
- Gold-set records were picked for having 3 or more sentences and an entity, so they are not a
  random sample of corpus-v1, though their answer length is the same.
