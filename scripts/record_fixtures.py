"""Record the DeepEval faithfulness judge's scores on the gold set, for replay in tests and CI.

This calls OpenAI and costs money. It needs OPENAI_API_KEY (in the environment or .env) and
stops itself once DeepEval reports ``--max-cost`` dollars spent, keeping every score recorded so
far. Running it again only pays for the scores still missing, so a stopped run can be finished
later (raise ``--max-cost`` if needed).

    uv run python scripts/record_fixtures.py               # shows the plan, asks before spending
    uv run python scripts/record_fixtures.py --limit 1     # a cheap trial on one record
    uv run python scripts/record_fixtures.py --regenerate  # new mutants from today's operators
    uv run python scripts/record_fixtures.py --refresh     # re-record every score

The texts scored are the gold set's records and the default battery's mutants of them (base seed
0), saved in tests/fixtures/gold-set/ the first time and reused after that, so finishing or
repeating a recording scores exactly the same texts as before. ``--regenerate`` makes the mutants
again from the current operators and replaces the saved ones. After a full run, scores that no
longer match a saved text or the installed judge are dropped from the file. See docs/fixtures.md.
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
from mtlj.engine.fixtures import FixtureStore, load_inputs, save_inputs
from mtlj.engine.generator import default_battery, generate_mutants
from mtlj.engine.runner import (
    DEFAULT_SAMPLES,
    MemorySink,
    RunAbortedError,
    build_tasks,
    run_judge_sync,
    task_key,
)
from mtlj.judges.deepeval.faithfulness import (
    DEFAULT_MODEL,
    DeepEvalFaithfulnessJudge,
    measure_faithfulness,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "judge" / "deepeval-faithfulness.jsonl"
DEFAULT_INPUTS = ROOT / "tests" / "fixtures" / "gold-set"
DEFAULT_MAX_COST = 3.00
# The base seed the saved mutants are generated with.
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
        self.refused = 0  # calls refused because of the cap
        self.reason = ""
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            reason = None
            if self.spent >= self.cap:
                reason = f"spending cap of ${self.cap:.2f} reached"
            elif self.unknown:
                # Without prices the cap can't be enforced, so stop rather than spend blind.
                reason = "DeepEval didn't report what a call cost"
            if reason is not None:
                self.refused += 1
                self.reason = reason
                raise BudgetExceededError(reason)

    def add(self, cost: float | None) -> None:
        with self._lock:
            self.calls += 1
            if cost is None:
                self.unknown += 1
            else:
                self.spent += cost


def gold_set_plan(inputs: Path, regenerate: bool):
    """The records and mutants to score: the saved ones, or new ones, saved for next time."""
    if not regenerate and (inputs / "records.jsonl").exists():
        return load_inputs(inputs)
    records = [r for r in load_corpus_v1().records if r.metadata["gold_set"]]
    mutants = generate_mutants(records, default_battery(), base_seed=BASE_SEED).mutants
    save_inputs(inputs, records, mutants)
    print(f"Saved {len(records)} records and {len(mutants)} mutants to {inputs}")
    return records, mutants


def plan_keys(keys: CacheKeys, tasks) -> set[str]:
    return {task_key(keys, task) for task in tasks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixture file")
    parser.add_argument(
        "--inputs", type=Path, default=DEFAULT_INPUTS, help="folder of the saved records/mutants"
    )
    parser.add_argument(
        "--regenerate", action="store_true", help="make the mutants again and save them"
    )
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

    # Before --limit, so the saved inputs are always the whole gold set.
    all_records, all_mutants = gold_set_plan(args.inputs, args.regenerate)
    records, mutants = all_records, all_mutants
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
    keys = CacheKeys(judge)
    tasks = build_tasks(records, mutants, args.samples)
    # Everything the replay test needs: all saved records and mutants, DEFAULT_SAMPLES each.
    needed = plan_keys(keys, build_tasks(all_records, all_mutants, DEFAULT_SAMPLES))
    # Only a run over all of that may drop scores; a trial (--limit, fewer --samples) never does.
    whole = args.limit is None and args.samples >= DEFAULT_SAMPLES
    store = FixtureStore(args.out, mode="record", refresh=args.refresh)
    complete = False
    try:
        # `in` follows --refresh: only scores recorded since the refresh began count.
        todo = sum(1 for key in plan_keys(keys, tasks) if key not in store)
        print(
            f"{len(records)} records + {len(mutants)} mutants x {args.samples} samples = "
            f"{len(tasks)} scorings with {judge.name} ({judge.model}, deepeval {judge.version})"
        )
        print(f"{len(tasks) - todo} already recorded in {args.out}; {todo} to call.")
        print(f"Spending cap: ${args.max_cost:.2f}")
        asked = todo and not args.yes
        if asked and input("Spend on these calls? [y/N] ").strip().lower() != "y":
            print("Nothing was spent.")
            return 1

        summary, stopped = None, None
        if todo:
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
            except RunAbortedError as exc:
                summary, stopped = exc.summary, str(exc)

        # The committed file only changes from one complete recording to another: a re-record
        # stopped part way (by the cap, say) would leave it mixing judge versions, which replay
        # refuses. Until then the new scores wait in the journal, and the next run resumes.
        complete = all(key in store for key in needed)
        # Keep this run's scores too: extra --samples were paid for.
        dropped = store.retain(needed | plan_keys(keys, tasks)) if complete and whole else 0
    finally:
        store.close(save=complete)

    if summary is not None:
        print()
        print(f"Called the judge {budget.calls} times; DeepEval reports ${budget.spent:.4f} spent.")
        print(f"{summary.errors} scorings failed.")
        if budget.calls:
            print(f"Average ${budget.spent / budget.calls:.5f} per scoring.")
    if budget.refused:
        print(f"The spending cap stopped {budget.refused} calls: {budget.reason}.")
    if stopped:
        print(f"Stopped early: {stopped}")
    if dropped:
        print(f"Dropped {dropped} scores for texts or judges no longer recorded.")
    if complete:
        print(f"{args.out} holds everything the replay test needs.")
        return 0
    print(
        f"{args.out} is unchanged: the recording isn't complete yet, and the new scores wait in "
        f"{store.partial_path.name}. Run this again (with a higher --max-cost if the cap was the "
        "reason) to finish."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
