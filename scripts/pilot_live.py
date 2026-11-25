"""Live pilot audit for MTLJ-89: `mtlj audit run` plus the cost and token counts the CLI lacks.

Needs OPENAI_API_KEY (environment or .env) and spends real money, capped by --max-cost.

    python -m uv run python scripts/pilot_live.py --judge deepeval-faithfulness \
        --limit 20 --output runs/pilot-default --expect-scorings 785 --max-cost 1.3

It generates mutants, scores them and writes the run directory exactly as `mtlj audit run` does
(it calls the same ``run_audit``), and wraps the judge's measure so each scoring's DeepEval
``evaluation_cost`` and token counts are kept. Those go in ``<output>/cost.json``. Without
this wrapper the CLI keeps no cost. A scoring that hits the cap fails with a permanent error, so
the cap is a hard stop. Then ``scripts/pilot_analysis.py live`` summarizes the run directories.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Budget:
    """Adds up what DeepEval says each call cost, and refuses new scorings past the cap."""

    def __init__(self, cap: float) -> None:
        self.cap = cap
        self.spent = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.scorings = 0
        self.unpriced = 0
        self.per_scoring: list[dict] = []
        self._lock = threading.Lock()

    def add(self, cost) -> None:
        with self._lock:
            self.scorings += 1
            if cost is None:
                self.unpriced += 1
                return
            tokens_in = int(getattr(cost, "input_tokens", 0) or 0)
            tokens_out = int(getattr(cost, "output_tokens", 0) or 0)
            self.spent += float(cost)
            self.input_tokens += tokens_in
            self.output_tokens += tokens_out
            self.per_scoring.append(
                {"cost": float(cost), "input_tokens": tokens_in, "output_tokens": tokens_out}
            )

    def check(self) -> None:
        with self._lock:
            if self.spent >= self.cap:
                raise RuntimeError(f"spending cap of ${self.cap:.2f} reached")


JUDGES = ("deepeval-faithfulness", "deepeval-faithfulness-penalized", "ragas-faithfulness")


class Priced(float):
    """A cost that is an estimate (Ragas), as a float with no token attributes."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--judge", default="deepeval-faithfulness")
    parser.add_argument("--dataset", default="corpus-v1")
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cost", type=float, required=True, help="US dollars")
    parser.add_argument("--expect-scorings", type=int, help="stop before spending if it differs")
    args = parser.parse_args()

    from mtlj.cli.main import _load_dataset, _select_battery
    from mtlj.engine.audit import check_can_start, run_audit
    from mtlj.engine.generator import generate_mutants
    from mtlj.engine.results import format_summary

    if args.judge not in JUDGES:
        sys.exit(f"unknown judge {args.judge}; choose from {JUDGES}")
    budget = Budget(args.max_cost)
    calls = {"n": 0, "input": 0, "output": 0}

    if args.judge == "ragas-faithfulness":
        # Needs a checkout with the Ragas adapter (MTLJ-65). Ragas reports no cost, so the
        # adapter's ``on_usage`` gives tiktoken estimates of each scoring's tokens, priced here
        # at gpt-4o-mini's list prices ($0.15 in, $0.60 out per million), as record_fixtures.py
        # does. One call to ``on_usage`` per scoring: its cost and tokens are estimates.
        from mtlj.judges.ragas import faithfulness as ragas
        from mtlj.judges.ragas.faithfulness import RagasFaithfulnessJudge

        def on_usage(prompt_tokens, completion_tokens):
            with budget._lock:
                calls["input"] += prompt_tokens
                calls["output"] += completion_tokens
            budget.add(Priced((prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000))

        def measure(input, output, passages):
            budget.check()
            return ragas.measure_faithfulness(
                input, output, passages, model=ragas.DEFAULT_MODEL, on_usage=on_usage
            )

        judge = RagasFaithfulnessJudge(ragas.DEFAULT_MODEL, measure=measure)
    else:
        from mtlj.judges.deepeval.faithfulness import (
            DEFAULT_MODEL,
            DeepEvalFaithfulnessJudge,
            measure_faithfulness,
        )

        penalized = args.judge == "deepeval-faithfulness-penalized"
        # DeepEval's evaluation_cost is a plain float, so the token counts are tallied where the
        # response's usage is turned into a cost. Each call of a scoring passes through here.
        from deepeval.models import OpenAIModel

        original_calculate_cost = OpenAIModel.calculate_cost

        def counting_calculate_cost(self, input_tokens, output_tokens):
            with budget._lock:
                calls["n"] += 1
                calls["input"] += int(input_tokens)
                calls["output"] += int(output_tokens)
            return original_calculate_cost(self, input_tokens, output_tokens)

        OpenAIModel.calculate_cost = counting_calculate_cost

        def measure(input, output, passages):
            budget.check()
            return measure_faithfulness(
                input,
                output,
                passages,
                model=DEFAULT_MODEL,
                penalize_ambiguous_claims=penalized,
                on_cost=budget.add,
            )

        judge = DeepEvalFaithfulnessJudge(
            DEFAULT_MODEL, penalize_ambiguous_claims=penalized, measure=measure
        )
    records = _load_dataset(args.dataset, args.limit)
    generation = generate_mutants(records, _select_battery("default"), base_seed=args.seed)
    plan = check_can_start(
        generation, judge, args.output, samples=args.samples, max_concurrency=args.concurrency
    )
    print(
        f"Planned: {len(generation.originals)} originals and {len(generation.mutants)} mutants, "
        f"{args.samples} scorings each: {len(plan)} scorings with {judge.name} ({judge.model}).",
        flush=True,
    )
    if args.expect_scorings is not None and len(plan) != args.expect_scorings:
        sys.exit(f"expected {args.expect_scorings} scorings, planned {len(plan)}: not running")

    start = time.perf_counter()
    results = run_audit(
        generation,
        judge,
        args.output,
        dataset=args.dataset,
        samples=args.samples,
        max_concurrency=args.concurrency,
    )
    wall = time.perf_counter() - start
    print(format_summary(results))
    info = {
        "judge": judge.name,
        "limit": args.limit,
        "samples": args.samples,
        "concurrency": args.concurrency,
        "planned_scorings": len(plan),
        "wall_seconds": wall,
        "spent_usd": budget.spent,
        "cap_usd": budget.cap,
        "priced_scorings": len(budget.per_scoring),
        "unpriced_scorings": budget.unpriced,
        "llm_calls": calls["n"],
        "input_tokens": calls["input"],
        "output_tokens": calls["output"],
        "per_scoring": budget.per_scoring,
    }
    (args.output / "cost.json").write_text(json.dumps(info) + "\n", encoding="utf-8")
    print(
        f"wall {wall:.0f}s, spent ${budget.spent:.4f} on {len(budget.per_scoring)} priced calls "
        f"({budget.unpriced} unpriced), {calls['n']} LLM calls, "
        f"tokens in/out {calls['input']}/{calls['output']}"
    )


if __name__ == "__main__":
    main()
