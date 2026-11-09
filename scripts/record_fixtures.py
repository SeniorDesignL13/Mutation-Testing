"""Record the DeepEval faithfulness judge's scores on the gold set, for replay in tests and CI.

This calls OpenAI and costs money. It needs OPENAI_API_KEY (in the environment or .env) and
stops itself once DeepEval reports ``--max-cost`` dollars spent, keeping every score recorded so
far. Running it again only pays for the scores still missing, so a stopped run can be finished
later (raise ``--max-cost`` if needed).

    uv run python scripts/record_fixtures.py               # shows the plan, asks before spending
    uv run python scripts/record_fixtures.py --limit 1     # a cheap trial on one record
    uv run python scripts/record_fixtures.py --refresh     # re-record every score

What's recorded must match what tests/judges/deepeval/test_faithfulness_replay.py replays:
the gold set's records, the default battery's mutants with base seed 0, and ``--samples``
samples each. See docs/fixtures.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path

from mtlj.data.ragtruth import load_corpus_v1
from mtlj.engine.cache import CacheKeys
from mtlj.engine.fixtures import FixtureStore
from mtlj.engine.generator import default_battery, generate_mutants
from mtlj.engine.runner import (
    DEFAULT_SAMPLES,
    MemorySink,
    RunAbortedError,
    build_tasks,
    judge_context,
    run_judge_sync,
)
from mtlj.judges.deepeval.faithfulness import (
    DEFAULT_MODEL,
    DeepEvalFaithfulnessJudge,
    measure_faithfulness,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "judge" / "deepeval-faithfulness.jsonl"
DEFAULT_MAX_COST = 3.00
# The base seed the replay test generates its mutants with.
BASE_SEED = 0


class BudgetExceededError(RuntimeError):
    """The spending cap is reached; no more judge calls."""


class Budget:
    """Adds up what each judge call cost and refuses new calls once ``cap`` dollars are spent.

    A call already in flight when the cap is reached still finishes, so the total can go over
    by up to ``max_concurrency`` calls (a fraction of a cent each on gpt-4o-mini).
    """

    def __init__(self, cap: float) -> None:
        self.cap = cap
        self.spent = 0.0
        self.calls = 0
        self.unknown = 0  # calls DeepEval couldn't price
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            if self.spent >= self.cap:
                raise BudgetExceededError(f"spending cap of ${self.cap:.2f} reached")
            if self.unknown:
                # Without prices the cap can't be enforced, so stop rather than spend blind.
                raise BudgetExceededError("DeepEval didn't report what a call cost")

    def add(self, cost: float | None) -> None:
        with self._lock:
            self.calls += 1
            if cost is None:
                self.unknown += 1
            else:
                self.spent += cost


def gold_set_plan():
    """The records and mutants the fixtures cover. Must match the replay test."""
    records = [r for r in load_corpus_v1().records if r.metadata["gold_set"]]
    generation = generate_mutants(records, default_battery(), base_seed=BASE_SEED)
    return records, generation.mutants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixture file")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST, help="US dollars")
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, help="only the first N gold-set records (a trial)")
    parser.add_argument("--refresh", action="store_true", help="re-record every score")
    parser.add_argument("--yes", action="store_true", help="don't ask before spending")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    import deepeval  # noqa: F401  (loads OPENAI_API_KEY from .env)

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (put it in .env). Nothing was spent.", file=sys.stderr)
        return 2

    records, mutants = gold_set_plan()
    if args.limit is not None:
        records = records[: args.limit]
        kept = {r.record_id for r in records}
        mutants = [m for m in mutants if m.record_id in kept]

    budget = Budget(args.max_cost)

    def measure(input, output, passages):
        budget.check()
        return measure_faithfulness(
            input, output, passages, model=DEFAULT_MODEL, on_cost=budget.add
        )

    judge = DeepEvalFaithfulnessJudge(DEFAULT_MODEL, measure=measure)
    with FixtureStore(args.out, mode="record", refresh=args.refresh) as store:
        keys = CacheKeys(judge)
        tasks = build_tasks(records, mutants, args.samples)
        todo = sum(
            1
            for t in tasks
            if args.refresh
            or store.get(keys(t.input, t.output, judge_context(t.passages), t.sample_index)) is None
        )
        print(
            f"{len(records)} records + {len(mutants)} mutants x {args.samples} samples = "
            f"{len(tasks)} scorings with {judge.name} ({judge.model}, deepeval {judge.version})"
        )
        print(f"{len(tasks) - todo} already recorded in {args.out}; {todo} to call.")
        print(f"Spending cap: ${args.max_cost:.2f}")
        if not todo:
            return 0
        if not args.yes and input("Spend on these calls? [y/N] ").strip().lower() != "y":
            print("Nothing was spent.")
            return 1

        try:
            summary = run_judge_sync(
                judge,
                records,
                mutants,
                MemorySink(),
                cache=store,
                samples=args.samples,
                max_concurrency=args.max_concurrency,
                # Once the cap is hit every call fails at once; stop soon after.
                max_consecutive_errors=2 * args.max_concurrency,
            )
            stopped = None
        except RunAbortedError as exc:
            summary, stopped = exc.summary, str(exc)
        recorded = store.recorded

    print(f"\nCalled the judge {budget.calls} times; DeepEval reports ${budget.spent:.4f} spent.")
    print(f"Recorded {recorded} new scores; {summary.errors} scorings failed.")
    if budget.calls:
        print(f"Average ${budget.spent / budget.calls:.5f} per scoring.")
    if stopped:
        print(f"Stopped early: {stopped}")
        print("Run again (with a higher --max-cost if the cap was the reason) to finish.")
        return 1
    return 0 if summary.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
