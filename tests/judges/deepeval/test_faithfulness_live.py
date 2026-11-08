"""MTLJ-68's check on real records: the gold set, through the real runner and OpenAI.

The gold set is the 20 records the MTLJ-31 spike scored, so the results can be held against
spike/scores.csv. About 300 OpenAI calls. Needs OPENAI_API_KEY, and is run on request:

    uv run pytest tests/judges/deepeval/test_faithfulness_live.py -m integration -s
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

import pytest

from mtlj.data.ragtruth import load_corpus_v1
from mtlj.engine.runner import JsonlSink, read_evaluations, run_judge_sync
from mtlj.judges.deepeval.faithfulness import DeepEvalFaithfulnessJudge
from mtlj.stats.detection import noise_floor

SPIKE_SCORES = Path(__file__).parents[3] / "spike" / "scores.csv"
SAMPLES = 5
# How far the gold set's mean score may sit from the spike's. One answer can swing by 0.3, but
# over 100 scorings with a noise floor near 0.05 the mean should move far less. A judge that
# calls every claim faithful shifts it by 0.046, which this has to catch.
MAX_MEAN_SHIFT = 0.03


def spike_scores() -> dict[str, list[float]]:
    """Each original's scores from the spike's default config, by source id."""
    scores = defaultdict(list)
    with SPIKE_SCORES.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if row["mutation_type"] == "original" and row["config"] == "default":
                scores[row["record_id"]].append(float(row["score"]))
    return dict(scores)


@pytest.mark.integration
def test_gold_set_matches_the_spike(tmp_path):
    import deepeval  # noqa: F401, RUF100  (loads OPENAI_API_KEY from .env)

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")

    records = [r for r in load_corpus_v1().records if r.metadata["gold_set"]]
    assert len(records) >= 20
    path = tmp_path / "evaluations.jsonl"
    judge = DeepEvalFaithfulnessJudge()

    with JsonlSink(path) as sink:
        summary = run_judge_sync(judge, records, [], sink, samples=SAMPLES, max_concurrency=4)
    rows = read_evaluations(path)

    source_ids = {r.record_id: r.metadata["source_id"] for r in records}
    scores = defaultdict(list)
    for row in rows:
        if row.status == "ok":
            scores[source_ids[row.record_id]].append(row.score)
    spike = spike_scores()
    ours = {source: mean(values) for source, values in scores.items()}
    theirs = {source: mean(spike[source]) for source in scores}
    shift = mean(ours.values()) - mean(theirs.values())

    print(f"\n{summary}")
    for row in rows:
        if row.status == "error":
            print(f"  {row.record_id} sample {row.sample_index}: {row.error}")
    print(f"{'record':>8} {'spike':>6} {'now':>6} {'diff':>7}")
    for source in sorted(ours):
        diff = ours[source] - theirs[source]
        print(f"{source:>8} {theirs[source]:6.3f} {ours[source]:6.3f} {diff:+7.3f}")
    print(f"{'mean':>8} {mean(theirs.values()):6.3f} {mean(ours.values()):6.3f} {shift:+7.3f}")
    print(f"noise floor: spike {noise_floor(spike.values()):.3f}", end="")
    print(f", now {noise_floor(scores.values()):.3f}")

    assert summary.errors == 0
    assert summary.ok == len(records) * SAMPLES
    for row in rows:
        assert (row.model, row.version) == (judge.model, judge.version)
        assert row.n_claims is not None
        assert row.verdict_yes is not None
    assert abs(shift) <= MAX_MEAN_SHIFT
