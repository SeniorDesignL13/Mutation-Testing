"""The Ragas adapter on real records: the gold set, through the real runner and OpenAI.

About 80 OpenAI calls (two per scoring). Needs OPENAI_API_KEY, and is run on request:

    uv run pytest tests/judges/ragas/test_faithfulness_live.py -m integration -s
"""

from __future__ import annotations

import os

import pytest

from mtlj.data.ragtruth import load_corpus_v1
from mtlj.engine.runner import JsonlSink, read_evaluations, run_judge_sync
from mtlj.judges.ragas.faithfulness import RagasFaithfulnessJudge

SAMPLES = 2


@pytest.mark.integration
def test_gold_set_scores_through_the_runner(tmp_path):
    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set")

    records = [r for r in load_corpus_v1().records if r.metadata["gold_set"]][:20]
    assert len(records) >= 20
    path = tmp_path / "evaluations.jsonl"
    judge = RagasFaithfulnessJudge()

    with JsonlSink(path) as sink:
        summary = run_judge_sync(judge, records, [], sink, samples=SAMPLES, max_concurrency=4)
    rows = read_evaluations(path)

    print(f"\n{summary}")
    for row in rows:
        if row.status == "error":
            print(f"  {row.record_id} sample {row.sample_index}: {row.error}")

    assert summary.errors == 0
    assert summary.ok == len(records) * SAMPLES
    for row in rows:
        assert (row.model, row.version) == (judge.model, judge.version)
        assert 0.0 <= row.score <= 1.0
        assert row.n_claims is not None
        assert row.verdict_yes is not None
        assert row.verdict_borderline == 0
