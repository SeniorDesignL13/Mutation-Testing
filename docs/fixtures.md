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

`tests/judges/deepeval/test_faithfulness_replay.py` replays all of them, with the network blocked.

Each line is one scoring: its key, the judge it came from, and the score with its verdict
counts. Lines are sorted by key, so recording again gives a small diff.

## When replay fails

`FixtureMissingError` means the file doesn't hold a scoring the test needs. Each score is keyed
on everything that decides it (the response cache's key, see `mtlj.engine.cache`):

- the judge's class, name, model and **version** (for DeepEval, the installed DeepEval version),
- its settings (`cache_settings()`),
- the exact question, answer and passages,
- the sample index.

So any of these makes the fixtures stale, on purpose:

- **Upgrading DeepEval.** Re-record in the same PR as the upgrade.
- **Changing an operator, the default battery, or the corpus.** Different mutants, different texts.
- **Changing the judge's config or prompt.**

A fixture recorded for other inputs would be testing something that no longer exists.

## Recording

You need `OPENAI_API_KEY` in `.env` (see [How to develop](DEVELOPER.md)).

```bash
uv run python scripts/record_fixtures.py --limit 1 --samples 1   # trial: a few cents at most
uv run python scripts/record_fixtures.py                         # the rest
```

The script prints how many calls it will make and asks before spending. Then it:

- **Only pays for what's missing.** Scores already in the file are reused, so re-running after
  a stop, or after the trial, finishes the job. `--refresh` re-records everything.
- **Stops at a spending cap**, `--max-cost` dollars (default 3.00) as DeepEval reports it, and
  stops at once if DeepEval can't price a call, rather than spend blind. The full gold set is
  about 765 scorings.
- **Keeps every score it paid for**, even if it crashes or you press Ctrl-C: each one is written
  to `<file>.partial` as it arrives, and merged into the file when the run ends (or the next run
  starts).

Then run `uv run pytest tests/judges/deepeval/test_faithfulness_replay.py` and commit the
fixture file.

## Recording in your own code

```python
from mtlj.engine.fixtures import FixtureStore
from mtlj.engine.runner import JsonlSink, run_judge_sync

with FixtureStore(path, mode="record") as fixtures, JsonlSink(results) as sink:
    run_judge_sync(judge, records, mutants, sink, cache=fixtures)

# Later, offline:
run_judge_sync(judge, records, mutants, sink, cache=FixtureStore(path, mode="replay"))
```

Failed scorings are never recorded. Replay checks every key before the run starts, so a
missing score fails at once, naming what's missing, instead of part way through.

## Fixtures or the response cache?

Both skip calls the judge already answered, and use the same keys.

- **The response cache** (`mtlj.engine.cache`) is yours alone: a SQLite file on your machine,
  never committed. On a miss it calls the judge.
- **A fixture file** is shared: plain JSONL, committed and reviewed. Replaying, a miss is an
  error.

## CI

CI never calls a real judge. Tests that do are marked `integration`, which pytest skips unless
you ask for them with `-m integration`. In CI (where `CI` is set), `tests/conftest.py` refuses to
run them at all, so selecting one by accident fails the build instead of spending money.
