"""MTLJ-31 spike: score every original answer and mutant with DeepEval faithfulness, N times each.

Appends one row per scoring to scores.csv as it completes, so a crash loses nothing.
Re-running skips rows already present. Rows are keyed on a hash of the scored text,
so a mutation edited after it was scored is scored again rather than silently reused.

Jobs run sample-major (every text once, then every text again, ...) and default config
before penalized, so an interrupted run still has balanced data.

Run from spike/ with the venv active:
    python run_spike.py                                   # everything in mutants.json
    python run_spike.py --types original,entity_swap      # originals + swaps only
    python run_spike.py --configs default                 # skip the penalize pass
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
MODEL = "gpt-4o-mini"
CONFIGS = {"default": {}, "penalize": {"penalize_ambiguous_claims": True}}
COLUMNS = [
    "record_id",
    "mutation_type",
    "sample",
    "score",
    "config",
    "text_hash",
    "n_claims",
    "verdicts",
]


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def load_texts(types: set[str]) -> list[dict]:
    """Originals plus mutants, each with the record's question and context attached."""
    records = json.loads((HERE / "records.json").read_text(encoding="utf-8"))
    mutants = json.loads((HERE / "mutants.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in records}
    texts = [{"record": r, "mutation_type": "original", "text": r["answer"]} for r in records]
    texts += [
        {"record": by_id[m["record_id"]], "mutation_type": m["mutation_type"], "text": m["text"]}
        for m in mutants
    ]
    return [t for t in texts if t["mutation_type"] in types]


def done_keys(out: Path) -> set[tuple]:
    if not out.exists():
        return set()
    with out.open(newline="", encoding="utf-8") as f:
        return {
            (r["record_id"], r["mutation_type"], r["text_hash"], r["config"], r["sample"])
            for r in csv.DictReader(f)
        }


async def score(job: dict) -> dict:
    """One fresh FaithfulnessMetric measurement; a new instance per call so no state is shared."""
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    metric = FaithfulnessMetric(
        model=MODEL,
        eval_mode="llm",
        async_mode=True,
        include_reason=False,
        **CONFIGS[job["config"]],
    )
    case = LLMTestCase(
        input=job["record"]["question"],
        actual_output=job["text"],
        retrieval_context=job["record"]["context"],
    )
    await metric.a_measure(case, _show_indicator=False)
    verdicts = [str(getattr(v.verdict, "value", v.verdict)) for v in metric.verdicts or []]
    return {
        "record_id": job["record"]["id"],
        "mutation_type": job["mutation_type"],
        "sample": job["sample"],
        "score": metric.score,
        "config": job["config"],
        "text_hash": job["hash"],
        "n_claims": len(metric.claims or []),
        "verdicts": "|".join(verdicts),
    }


async def run(jobs: list[dict], out: Path, concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    new_file = not out.exists()
    started, finished, failed = time.perf_counter(), 0, 0

    with out.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()

        async def worker(job: dict) -> None:
            nonlocal finished, failed
            async with sem:
                try:
                    row = await score(job)
                except Exception as exc:
                    failed += 1
                    print(
                        f"  FAILED {job['record']['id']} {job['mutation_type']} s{job['sample']} "
                        f"{job['config']}: {type(exc).__name__}: {exc}"
                    )
                    return
            writer.writerow(row)
            f.flush()
            finished += 1
            if finished % 20 == 0 or finished + failed == len(jobs):
                elapsed = time.perf_counter() - started
                eta = elapsed / finished * (len(jobs) - finished - failed)
                print(
                    f"  [{finished + failed}/{len(jobs)}] {elapsed / 60:.1f} min elapsed, "
                    f"~{eta / 60:.1f} min left"
                    f"{f', {failed} failed' if failed else ''}"
                )

        await asyncio.gather(*(worker(j) for j in jobs))

    print(
        f"done: {finished} scored, {failed} failed"
        f"{' (re-run to retry the failures)' if failed else ''}; results in {out.name}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--types",
        default="original,entity_swap,verbosity_pad,reformat",
        help="comma-separated mutation types to score; 'original' is the unmutated answer",
    )
    parser.add_argument(
        "--configs", default="default,penalize", help=f"comma-separated, from {list(CONFIGS)}"
    )
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, help="score at most this many jobs (smoke tests)")
    parser.add_argument("--out", type=Path, default=HERE / "scores.csv")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    load_dotenv(HERE / ".env")
    if sys.platform == "win32":
        # The default Proactor loop spews "Event loop is closed" when HTTP clients are GC'd at exit.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    configs = args.configs.split(",")
    texts = load_texts(set(args.types.split(",")))
    for t in texts:
        t["hash"] = text_hash(t["text"])
    done = done_keys(args.out)
    jobs = [
        {**t, "config": config, "sample": str(sample)}
        for config in configs
        for sample in range(args.samples)
        for t in texts
        if (t["record"]["id"], t["mutation_type"], t["hash"], config, str(sample)) not in done
    ][: args.limit]

    counts = {}
    for t in texts:
        counts[t["mutation_type"]] = counts.get(t["mutation_type"], 0) + 1
    print(f"texts: {counts}   configs: {configs}   samples: {args.samples}")
    print(
        f"{len(jobs)} scorings to run ({len(done)} rows already in {args.out.name}), "
        f"concurrency {args.concurrency}"
    )
    if jobs:
        asyncio.run(run(jobs, args.out, args.concurrency))


if __name__ == "__main__":
    main()
