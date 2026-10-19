"""MTLJ-31 spike, Tasks 1 + 2: does DeepEval FaithfulnessMetric run on this machine?

Checks, in order:
  1. One FaithfulnessMetric call on a hardcoded case, under a hard timeout. Tries the
     async path first (documented to hang on Windows); falls back to async_mode=False.
  2. Default vs penalize_ambiguous_claims=True on the same case, side by side.
  3. Non-determinism: the same metric on the same input N times.

Run from the repo root:
    spike/.venv/Scripts/python.exe spike/verify_deepeval.py
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib.metadata
import os
import sys
import time

CASE = dict(
    input="What is the return window?",
    actual_output="You can return items within 30 days.",
    retrieval_context=["Our policy allows returns within 30 days of purchase."],
)


def check_environment() -> None:
    """Print versions and settings that decide how the metric behaves; exit if no key."""
    from deepeval.config.eval_mode import resolve_eval_mode
    from deepeval.config.settings import get_settings

    settings = get_settings()
    print(f"python            {sys.version.split()[0]} ({sys.platform})")
    print(f"deepeval          {importlib.metadata.version('deepeval')}")
    print(
        f"eval mode         {resolve_eval_mode()}  "
        f"(DEEPEVAL_EVAL_MODE={settings.DEEPEVAL_EVAL_MODE!r})"
    )
    print(f"TEMPERATURE       {settings.TEMPERATURE!r}  (None -> deepeval uses 0.0)")
    key = os.environ.get("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    print(f"OPENAI_API_KEY    {'set' if key else 'MISSING'}")
    if not key:
        sys.exit("\nOPENAI_API_KEY is not set. Set it (env var or repo-root .env) and re-run.")
    if str(resolve_eval_mode()) != "llm":
        print("WARNING: eval mode is not 'llm'. Run: deepeval set-eval-mode llm")


def measure(timeout_s: float, async_mode: bool, **metric_kwargs) -> dict:
    """Run one FaithfulnessMetric.measure in a worker thread, failing loudly on a hang."""
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    metric = FaithfulnessMetric(async_mode=async_mode, **metric_kwargs)
    case = LLMTestCase(**CASE)

    start = time.perf_counter()
    pool = cf.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(metric.measure, case, _show_indicator=False)
    try:
        future.result(timeout=timeout_s)
    except cf.TimeoutError:
        pool.shutdown(wait=False)
        raise TimeoutError(
            f"measure() hung for >{timeout_s:.0f}s (async_mode={async_mode})"
        ) from None
    pool.shutdown(wait=False)

    return {
        "score": metric.score,
        "reason": metric.reason,
        "truths": list(getattr(metric, "truths", []) or []),
        "claims": list(getattr(metric, "claims", []) or []),
        "verdicts": [
            (str(getattr(v.verdict, "value", v.verdict)), v.reason)
            for v in (getattr(metric, "verdicts", []) or [])
        ],
        "model": metric.evaluation_model,
        "elapsed_s": time.perf_counter() - start,
    }


def print_result(label: str, r: dict) -> None:
    """Print score plus the intermediate claims/truths/verdicts."""
    print(f"\n--- {label} ---")
    print(f"model={r['model']}  score={r['score']}  elapsed={r['elapsed_s']:.1f}s")
    print(f"reason: {r['reason']}")
    print("truths:", *[f"  - {t}" for t in r["truths"]] or ["  (none)"], sep="\n")
    print("claims:", *[f"  - {c}" for c in r["claims"]] or ["  (none)"], sep="\n")
    print("verdicts:", *[f"  - {v}: {why}" for v, why in r["verdicts"]] or ["  (none)"], sep="\n")


def main() -> None:
    """Run the three checks and print a summary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--sync", action="store_true", help="skip the async attempt")
    args = parser.parse_args()

    print("=== environment ===")
    check_environment()

    # Task 1: single call; async first, sync fallback.
    print("\n=== Task 1: single FaithfulnessMetric call ===")
    async_mode = not args.sync
    try:
        first = measure(args.timeout, async_mode=async_mode)
    except Exception as exc:
        print(f"FAILED (async_mode={async_mode}): {type(exc).__name__}: {exc}")
        if not async_mode:
            raise
        print("Retrying with async_mode=False ...")
        async_mode = False
        first = measure(args.timeout, async_mode=False)
    print_result(f"default (async_mode={async_mode})", first)
    print(f"\n=> working path: async_mode={async_mode}")

    # Task 2: penalize_ambiguous_claims.
    print("\n=== Task 2: default vs penalize_ambiguous_claims=True ===")
    penalized = measure(args.timeout, async_mode=async_mode, penalize_ambiguous_claims=True)
    print_result("penalize_ambiguous_claims=True", penalized)
    print(f"\n{'config':<12}{'score':>8}  verdicts")
    for name, r in (("default", first), ("penalized", penalized)):
        print(f"{name:<12}{r['score']:>8.3f}  {[v for v, _ in r['verdicts']]}")

    # Task 1b: non-determinism.
    print(f"\n=== Task 1b: {args.runs} repeated calls, same input, default config ===")
    runs = []
    for i in range(args.runs):
        r = measure(args.timeout, async_mode=async_mode)
        runs.append(r)
        print(
            f"run {i + 1}: score={r['score']}  claims={len(r['claims'])}  "
            f"verdicts={[v for v, _ in r['verdicts']]}  ({r['elapsed_s']:.1f}s)"
        )
    scores = [r["score"] for r in runs]
    print(f"\nscores: {scores}")
    print(f"distinct scores: {len(set(scores))}")
    print(f"distinct claim lists: {len({tuple(r['claims']) for r in runs})}")
    print(f"distinct reasons: {len({r['reason'] for r in runs})}")
    if len(set(scores)) == 1:
        print(
            "All scores identical. measure() called directly does not read DeepEval's test-run "
            "cache (that is only used by evaluate()/`deepeval test run`), so identical scores "
            "most likely reflect temperature=0 plus a trivially easy case. Distinct reasons/claims "
            "above show whether the LLM output itself varied."
        )

    # Worker threads from a hung async attempt would keep the process alive.
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
