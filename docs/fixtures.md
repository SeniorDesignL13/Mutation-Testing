# Record and replay

A real judge costs money, needs a network and an API key, and gives slightly different scores
every time. So we call it **once**, record its scores in a file committed with the tests, and
**replay** them everywhere else: in tests, in CI, and while developing operators and metrics.

| | Record | Replay |
|---|---|---|
| Calls the judge | Only for scores not in the file yet | **Never** |
| Needs `OPENAI_API_KEY` | Yes | No |
| Costs | Real money (capped) | Nothing |
| Where | On your machine, on request | Default test suite, CI |

The code is `mtlj.engine.fixtures.FixtureStore`. Pass it to the runner as its `cache`.

## What's recorded

`tests/fixtures/judge/deepeval-faithfulness.jsonl` holds the DeepEval faithfulness judge's
scores (gpt-4o-mini, default config) for:

- the 20 gold-set records of corpus v1,
- every mutant the default battery makes of them (base seed 0),
- 5 samples of each (the 2026-10-20 decision).

The texts themselves are saved next to it, in `tests/fixtures/gold-set/` (`records.jsonl` and
`mutants.jsonl`). `tests/judges/deepeval/test_faithfulness_replay.py` replays all of them, with
the network blocked.

Each line of the scores file is one scoring: its key, the judge it came from, and the score with
its verdict counts. Lines are sorted by key, so recording again gives a small diff.

## What doesn't break replay

Replay scores the saved texts, not mutants made fresh from today's operators, and it plays the
judge as it was recorded: same name, model and DeepEval version, read from the file. So none of
these break it:

- **Changing an operator or the default battery.** The saved mutants are what was scored.
- **Upgrading DeepEval or spaCy** (Dependabot does every week). Replay never runs DeepEval, and
  never runs spaCy to make mutants.

The fixtures keep testing the texts and judge they were recorded with. Re-record when you want
that brought up to date, for example after an operator change you want reflected in the replayed
scores, or a DeepEval upgrade whose behaviour you want to check.

## When replay fails

- **`FixtureMissingError`** means the scores file doesn't hold a scoring the saved texts need.
  Each score is keyed on everything that decides it (the response cache's key, see
  `mtlj.engine.cache`): the judge's class, name, model, version and settings, the exact
  question, answer and passages, and the sample index. Usually a recording stopped before the
  end: run the recorder again to finish it.
- **"mixes scores from several judges or versions"** shouldn't happen with the recorder, which
  only writes the file once a recording is complete. It means the file was edited or recorded
  some other way: record it again.
- **"was recorded with another judge"** means the replay judge differs from the recorded one in
  more than its version. Build it from `FixtureStore.judge` (see below).

## Recording

You need `OPENAI_API_KEY` in `.env` (see [How to develop](DEVELOPER.md)).

```bash
uv run python scripts/record_fixtures.py --limit 1 --samples 1   # trial: a few cents at most
uv run python scripts/record_fixtures.py                         # the rest
uv run python scripts/record_fixtures.py --regenerate            # mutants made again
```

The script prints how many calls it will make and asks before spending. Then it:

- **Only pays for what's missing.** Scores already in the file are reused, so re-running after
  a stop, or after the trial, finishes the job. `--refresh` re-records everything.
- **Stops at a spending cap**, `--max-cost` dollars (default 3.00) as DeepEval reports it, and
  stops at once if DeepEval can't price a call, rather than spend blind. The full gold set is
  about 765 scorings.
- **Scores the saved texts.** The first run saves the gold set and its mutants in
  `tests/fixtures/gold-set/`, and later runs reuse them, so finishing a recording scores the same
  texts. `--regenerate` makes the mutants again from the current operators and saves them.
- **Drops stale scores** after a full run (all records, 5 samples): scores for texts no longer
  saved, or from another DeepEval version, are removed, so the file only holds what replay uses.
  A trial (`--limit`, or fewer `--samples`) never drops anything.
- **Only changes the committed file when the recording is complete**, every saved text at 5
  samples. A run stopped by the cap, a crash, Ctrl-C or a trial leaves the file as it was, so
  the replay test never sees a half-finished re-record.
- **Keeps every score it paid for** meanwhile: each one is written to `<file>.partial` as it
  arrives (git-ignored), and the next run picks them up and pays only for the rest.
- **Counts what failed calls cost too.** The cap covers LLM calls billed before a scoring
  failed, and a call DeepEval can't price stops the run.

Then run `uv run pytest tests/judges/deepeval/test_faithfulness_replay.py` and commit the
scores file together with `tests/fixtures/gold-set/`.

## Recording in your own code

```python
from mtlj.engine.fixtures import FixtureStore, load_inputs, save_inputs
from mtlj.engine.runner import JsonlSink, run_judge_sync

with FixtureStore(path, mode="record") as fixtures, JsonlSink(results) as sink:
    run_judge_sync(judge, records, mutants, sink, cache=fixtures)
save_inputs(inputs_dir, records, mutants)

# Later, offline, as the judge that was recorded:
store = FixtureStore(path, mode="replay")
recorded = store.judge
replay = store.replay_as(MyJudge(recorded.model, **recorded.settings, measure=refuse))
run_judge_sync(replay, *load_inputs(inputs_dir), sink, cache=store)
```

Each line stores the judge's whole identity (class, name, model, version, score range and
settings), so the replay judge can be built from it. `replay_as` checks it matches in everything
but the version, and gives it the recorded version.

Failed scorings are never recorded. Replay checks every key before the run starts, so a
missing score fails at once, naming what's missing, instead of part way through.

## Fixtures or the response cache?

Both skip calls the judge already answered, and use the same keys.

- **The response cache** (`mtlj.engine.cache`) is yours alone: a SQLite file on your machine,
  never committed. On a miss it calls the judge.
- **A fixture file** is shared: plain JSONL, committed and reviewed. Replaying, a miss is an
  error.

## CI

CI never calls a real judge, and doesn't rely on anyone remembering a marker:

- Tests that call one are marked `integration`, which pytest skips unless you ask for them with
  `-m integration`. In CI (where `CI` is true), `tests/conftest.py` refuses to run them at all.
- In CI, every test also runs with the outside network blocked: a connection to, or name
  lookup of, anything but loopback and the test database fails the test. So an unmarked test
  that would reach a real judge fails instead of spending money.
