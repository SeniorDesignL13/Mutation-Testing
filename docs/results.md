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

`schema_version` is 1. Any change to the format, even a new field, bumps it, and `read_results`
refuses every version but its own. To change the format, update the models in
`src/mtlj/engine/results.py`, bump `SCHEMA_VERSION`, and update this page.

## Format

```json
{
  "schema_version": 1,
  "run": {},
  "noise": {},
  "operators": [],
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

`noise`, `variance` and `operators` are only filled in when `status` is `complete`. They are
null, null and empty otherwise:

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

One entry per operator, sorted by name: `operator`, `family`, `operator_errors`, `overall` and
`by_intensity`. `operator_errors` counts mutants the operator crashed on, which are in no rate's
denominator and no `excluded` count. A rate with a high count is measured on a smaller sample
than it looks. `by_intensity` lists `{"intensity", "result"}` for each intensity in the battery, including ones
with no mutants.

`overall` pools the operator's intensities. A record appears once per intensity there, and those
mutants share one original, so one unlucky draw can move them together. Its interval is
narrower than independent mutants would give. Use `by_intensity` for anything that depends on
the interval's width.

Both `overall` and `result` hold:

| Field | Meaning |
|---|---|
| `metric` | `detection_rate` or `spurious_sensitivity` |
| `rate`, `ci` | The rate and its Wilson interval. Null when nothing is testable |
| `testable` | The denominator |
| `excluded` | Mutants left out, by reason: `no_change`, `no_scores`, and `no_headroom` for detection rate |
| `tau`, `n_samples` | The threshold, at N scores on both sides |
| `paired_delta` | `mean`, `ci`, `sd` and `n` of the drop in score. Negative means scores rose |
| `effect_size` | The mean drop in units of sigma. Null when sigma is 0 |

Detection rate adds `detected`. Spurious sensitivity adds `flagged`, `penalized`, `rewarded`,
`p_value` and `above_chance`. Read its rate against `alpha`, not against 0. `p_value` and
`above_chance` are null when the test wasn't run. That's when nothing is testable, and in an
`overall` that pools several intensities, because the test assumes independent mutants: a judge blind to the change came out above chance 7.9% of the time when
pooled, in a simulation with 20 records, 4 intensities and N = 5.

### `variance`

The report from `mtlj.stats.variance`: `level`, a `baseline` estimate from the originals, and one
entry in `conditions` per operator and intensity, with its `ratio` to the baseline. An intensity
with no mutants has a null `estimate`.
