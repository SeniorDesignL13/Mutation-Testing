# Audit results

Every `mtlj audit run` leaves one directory:

| File | What it holds |
|---|---|
| `results.json` | The results. This is what the dashboard, CI and analysis read |
| `evaluations.jsonl` | Every scoring, one line each, including failures and verdict counts |
| `originals.jsonl`, `mutants.jsonl`, `skipped.jsonl` | What was scored, and what was skipped and why |
| `run.json` | The seed, battery and library versions behind the mutants |

`mtlj audit show <run directory>` prints `results.json`. `mtlj.engine.results.read_results` loads
it in Python. [metrics.md](metrics.md) defines every metric below.

The file holds no timestamps, so the same run always gives identical bytes.

## Versioning

`schema_version` is 1, and `read_results` refuses every version but its own. Bump it only for a
breaking change: a field removed, renamed or given a new meaning. A new field doesn't bump it.
Readers ignore fields they don't know, so an older `mtlj` still reads a newer file, and a new
field needs a default so files written before it still read. Other readers, like the dashboard,
should ignore unknown fields too. To change the format, update the models in
`src/mtlj/engine/results.py` and this page, and bump `SCHEMA_VERSION` in the same change if it
breaks.

## Format

```json
{
  "schema_version": 1,
  "run": {},
  "noise": {},
  "operators": [],
  "correction": {},
  "variance": {}
}
```

### `run`

| Field | Meaning |
|---|---|
| `judge` | `name`, `model` and `version`. A rate belongs to a judge and its config, so each has its own run |
| `dataset`, `n_records` | Which dataset the records came from, and how many were used |
| `samples` | N, how many times each text was meant to be scored |
| `alpha` | The false-detection rate |
| `ci_level` | The confidence level of the intervals |
| `base_seed`, `battery`, `drop_repeated_doses` | How the mutants were generated |
| `status` | `complete`, `aborted` or `insufficient_data` |
| `reason` | Why the status isn't `complete`, otherwise null |
| `scorings` | `total`, `ok`, `errors` and `errors_by_type` |

`noise`, `correction`, `variance` and `operators` are only filled in when `status` is
`complete`. Otherwise the first three are null and `operators` is empty:

- **`aborted`:** the run stopped early, because the judge went down or it was stopped with
  Ctrl+C, SIGTERM or SIGHUP. Its rates would mix scored mutants with ones never
  reached, so none are reported. What was scored is still in `evaluations.jsonl`.
- **`insufficient_data`:** no original has the two or more scores that measure noise.

A complete run can still have failed scorings. Check `scorings.errors`.

### `noise`

`sigma` is the noise floor. `n_originals` is how many originals it comes from.

Read a small run's sigma with care. If every original scored the same on every sample, sigma and
tau are 0, so the smallest move counts. The first three gold-set records do this: they score 1.0
every time, so a run of just those flags a padded answer for one lower score in five.

### `operators`

One entry per operator, sorted by name: `operator`, `family`, `operator_errors`, `overall`,
`by_intensity` and `dose_response`. `operator_errors` counts mutants the operator crashed on,
which are in no rate's denominator and no `excluded` count. A rate with a high count is
measured on a smaller sample than it looks. `by_intensity` lists `{"intensity", "result"}` for
each intensity in the battery, including ones with no mutants.

`overall` pools the operator's intensities. A record appears once per intensity there, and those
mutants share one original, so one unlucky draw can move them together. Its Wilson interval
treats those mutants as independent, so it's too narrow. Its bootstrap interval resamples whole
records, so it holds. Use `by_intensity`, or the pooled `bootstrap_ci`, for anything that
depends on an interval's width. An operator with one intensity has nothing to pool, so its
`overall` is that intensity's result.

Both `overall` and `result` hold:

| Field | Meaning |
|---|---|
| `metric` | `detection_rate` or `spurious_sensitivity` |
| `rate` | The rate. Null when nothing is testable |
| `wilson_ci` | The Wilson interval, as `[low, high]` |
| `bootstrap_ci` | The record-level bootstrap interval: `ci`, `level`, `n_resamples`, `n_valid` and `seed`. Its `ci` is null when no resample had a rate |
| `headline_interval` | Which interval to show first: `bootstrap` from 100 testable mutants, `wilson` below that |
| `testable` | The denominator |
| `excluded` | Mutants left out, by reason: `no_change`, `no_scores`, and `no_headroom` for detection rate |
| `tau`, `n_samples` | The threshold, at N scores on both sides |
| `paired_delta` | `mean`, `ci`, `sd` and `n` of the drop in score. Negative means scores rose |
| `effect_size` | The mean drop in units of sigma. Null when sigma is 0 |
| `p_value`, `above_chance` | The exact binomial test of the hits against `alpha`, and whether p < `alpha` |
| `p_adjusted`, `above_chance_corrected` | The same test after the correction below, and whether the adjusted p < `q` |

`wilson_ci`, `bootstrap_ci` and `headline_interval` are null when nothing is testable. Show the
interval `headline_interval` names, and say which it is: intervals from runs of different sizes
aren't the same kind.

Detection rate adds `detected`. Spurious sensitivity adds `flagged`, `penalized` and `rewarded`.
Read either rate against `alpha`, not against 0: a judge blind to the operator still hits about
`alpha` of mutants through noise. The four test fields are null when the test wasn't run. That's
when nothing is testable, and in an `overall` that pools several intensities, because the test
assumes independent mutants. A judge blind to the change came out above chance 7.9% of the time
when pooled, in a simulation with 20 records, 4 intensities and N = 5.

`dose_response` is the operator's fitted curve and onset
([metrics.md](metrics.md#dose-response-curve)). It's null unless two or more intensities above 0
have a testable mutant, so it's null for the reformatting operators, which run at 1.0 only.
Intensity 0 changes nothing and isn't fitted.

| Field | Meaning |
|---|---|
| `onset` | The intensity at which the hit rate is `benchmark` of the way from `alpha` to 1. Null when the best fit is no response at any intensity, 0 when every mutant at every intensity is a hit |
| `onset_ci` | Its profile-likelihood interval, `[low, high]`. `high` is null when the data can't rule out a judge that never responds (infinite) |
| `slope` | The slope on log intensity. Null when `onset` is null or 0 |
| `responds` | Whether the hit rate rises above chance somewhere: `high` isn't null |
| `in_range` | Whether `onset` lies between the lowest and highest intensity fitted |
| `fit_p_value` | How well the curve fits the rates. Null with two intensities |
| `alpha`, `benchmark`, `level` | The curve's floor, the onset's benchmark (0.10) and the interval's level |
| `points` | Each intensity fitted: `intensity`, `hits`, `testable` and the curve's `fitted_rate` there |

### `correction`

Every `by_intensity` test in the run, of every operator, degrading and preserving alike, is one
family for the Benjamini-Hochberg correction
([metrics.md](metrics.md#multiple-comparison-correction)). `method` is `benjamini-hochberg`,
`q` is `alpha`, and `n_tests` counts the tests that were run. Pooled figures aren't tested, so
they aren't in it. Judge a result by `above_chance_corrected`. `above_chance` stays in the file
so you can see what the correction changed.

### `variance`

The report from `mtlj.stats.variance`: `level`, a `baseline` estimate from the originals, and one
entry in `conditions` per operator and intensity, with its `ratio` to the baseline. An intensity
with no mutants has a null `estimate`. Its shape belongs to `mtlj.stats.variance`, which
documents it.
