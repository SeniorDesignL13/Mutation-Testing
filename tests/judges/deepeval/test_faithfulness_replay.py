"""The DeepEval faithfulness judge on the gold set, replayed from recorded scores (MTLJ-90).

Runs in the default suite and in CI with no API key and no network: every score comes from
tests/fixtures/judge/deepeval-faithfulness.jsonl, recorded with scripts/record_fixtures.py, on the
records and mutants saved in tests/fixtures/gold-set/. Replay uses those saved inputs and the
judge identity in the file, so editing an operator or upgrading DeepEval doesn't break it. See
docs/fixtures.md.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean

from mtlj.engine.fixtures import FixtureStore, load_inputs
from mtlj.engine.runner import DEFAULT_SAMPLES, MemorySink, run_judge_sync
from mtlj.judges.deepeval.faithfulness import DeepEvalFaithfulnessJudge

FIXTURES_DIR = Path(__file__).parents[2] / "fixtures"
FIXTURES = FIXTURES_DIR / "judge" / "deepeval-faithfulness.jsonl"
GOLD_SET = FIXTURES_DIR / "gold-set"


def never_called(input, output, passages):
    raise AssertionError("replay must not call the judge")


def replay_judge(store: FixtureStore) -> DeepEvalFaithfulnessJudge:
    """The judge the fixtures were recorded with, built from its recorded settings, with a
    measure that refuses to run. ``replay_as`` checks it matches and sets the recorded version.
    """
    recorded = store.judge
    assert recorded is not None, f"{store.path} holds no fixtures"
    judge = DeepEvalFaithfulnessJudge(recorded.model, **recorded.settings, measure=never_called)
    return store.replay_as(judge)


def test_gold_set_replays_from_fixtures_with_no_judge_call_and_no_network(no_network):
    records, mutants = load_inputs(GOLD_SET)
    store = FixtureStore(FIXTURES, mode="replay")
    sink = MemorySink()

    summary = run_judge_sync(
        replay_judge(store), records, mutants, sink, cache=store, samples=DEFAULT_SAMPLES
    )

    expected = DEFAULT_SAMPLES * (len(records) + len(mutants))
    assert summary.ok == summary.cache_hits == expected
    assert summary.errors == 0
    rows = sink.evaluations
    assert all(r.cached and r.attempts == 0 for r in rows)
    assert all(r.n_claims is not None and r.verdict_yes is not None for r in rows)

    # The recorded scores mean something: swapping entities into an answer makes it less
    # faithful to its passages, on average, than the original.
    originals = mean(r.score for r in rows if r.kind == "original")
    swapped = mean(r.score for r in rows if r.operator_name == "entity_swap")
    assert swapped < originals
