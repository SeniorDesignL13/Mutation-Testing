"""
MTLJ-31 environment check.

Run:  python check_env.py

Answers four questions:
  1. Does DeepEval run at all on this machine (Windows async hang)?
  2. Is it in the right eval mode?
  3. Is the judge actually non-deterministic?
  4. Does penalize_ambiguous_claims change the score?
"""

import os
import statistics
import sys
import time

# Load .env from the current directory before anything reads os.environ.
try:
    from dotenv import load_dotenv

    _loaded = load_dotenv()
    _envfile = os.path.join(os.getcwd(), ".env")
    print(f"[.env] exists: {os.path.exists(_envfile)}   loaded: {_loaded}")
except ImportError:
    print("[.env] python-dotenv not installed; relying on shell environment")

print("=" * 60)
print("MTLJ-31 ENVIRONMENT CHECK")
print("=" * 60)

# ---------- 1. Preflight ----------
print("\n[1] PREFLIGHT")

key = os.environ.get("OPENAI_API_KEY")
if not key:
    print("  FAIL: OPENAI_API_KEY is not set.")
    print('        PowerShell:  $env:OPENAI_API_KEY="sk-..."')
    print("        or put  OPENAI_API_KEY=sk-...  in a .env file here")
    print(f"        cwd = {os.getcwd()}")
    print(f"        files here: {[f for f in os.listdir('.') if f.startswith('.env')]}")
    sys.exit(1)
print(f"  OK: API key present (ends ...{key[-4:]})")

try:
    import deepeval
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase
except Exception as e:
    print(f"  FAIL: import error: {e}")
    sys.exit(1)

print(f"  OK: deepeval {deepeval.__version__}")
print(f"  OK: python {sys.version.split()[0]}")
print("  OK: model = gpt-4o-mini (set explicitly, not relying on defaults)")

# ---------- 2. Single call, sync mode ----------
print("\n[2] SINGLE CALL  (async_mode=False to dodge the Windows hang)")

CASE = dict(
    input="What is the return window?",
    actual_output="You can return items within 30 days of purchase.",
    retrieval_context=["Our policy allows returns within 30 days of purchase."],
)

MODEL = "gpt-4o-mini"


def score_once(**kwargs):
    m = FaithfulnessMetric(model=MODEL, async_mode=False, **kwargs)
    m.measure(LLMTestCase(**CASE))
    return m


t0 = time.time()
try:
    m = score_once()
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
    print("        If this hung rather than errored, move to WSL.")
    sys.exit(1)

print(f"  OK: score = {m.score}   ({time.time() - t0:.1f}s)")
print(f"      claims found: {len(getattr(m, 'claims', []) or [])}")
if getattr(m, "verdicts", None):
    verdicts = [v.verdict for v in m.verdicts]
    print(f"      verdicts: {verdicts}")

# ---------- 3. Async mode (is the hang real on this box?) ----------
print("\n[3] ASYNC MODE  (the path muteval reported hanging on Windows)")
t0 = time.time()
try:
    ma = FaithfulnessMetric(model=MODEL, async_mode=True)
    ma.measure(LLMTestCase(**CASE))
    print(f"  OK: async works too, score = {ma.score}  ({time.time() - t0:.1f}s)")
    print("      -> you can use either mode")
except Exception as e:
    print(f"  WARN: async failed ({type(e).__name__}). Use async_mode=False everywhere.")

# ---------- 4. Non-determinism ----------
print("\n[4] NON-DETERMINISM  (5 runs, identical input)")
scores = []
for i in range(5):
    s = score_once().score
    scores.append(s)
    print(f"      run {i + 1}: {s}")

sd = statistics.stdev(scores) if len(set(scores)) > 1 else 0.0
print(f"\n  scores: {scores}")
print(f"  stdev:  {sd:.4f}")
if sd == 0.0:
    print("  >> IDENTICAL. Noise floor ~0 on this case.")
    print("     Not a bug. Report it: effect sizes will be huge, implied N tiny.")
    print("     Worth re-checking on a longer, multi-claim answer later.")
else:
    print("  >> Judge is non-deterministic. This is the noise you're measuring.")

# ---------- 5. penalize_ambiguous_claims ----------
print("\n[5] penalize_ambiguous_claims")
print("    Default scoring counts BORDERLINE verdicts as faithful.")
print("    With the flag on, only YES counts.")

UNSUPPORTED = dict(
    input="What is the return window?",
    actual_output=(
        "You can return items within 30 days of purchase. Shipping costs are also refunded in full."
    ),
    retrieval_context=["Our policy allows returns within 30 days of purchase."],
)


def score_case(case, **kwargs):
    m = FaithfulnessMetric(model=MODEL, async_mode=False, **kwargs)
    m.measure(LLMTestCase(**case))
    return m


a = score_case(UNSUPPORTED)
b = score_case(UNSUPPORTED, penalize_ambiguous_claims=True)
print("\n  answer has one supported claim + one the context never mentions")
print(f"    default                        : {a.score}")
print(f"    penalize_ambiguous_claims=True : {b.score}")
if a.score == b.score:
    print("  >> No difference here. Check verdicts to see why.")
else:
    print("  >> Flag matters. Run the spike under BOTH configs.")
if getattr(a, "verdicts", None):
    print(f"    default verdicts: {[v.verdict for v in a.verdicts]}")

# ---------- Summary ----------
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"  deepeval        : {deepeval.__version__}")
print(f"  single call     : OK, score {m.score}")
print(f"  noise (5 runs)  : stdev {sd:.4f}  {'(fully stable)' if sd == 0 else ''}")
print(f"  penalize flag   : {a.score} -> {b.score}")
print("\nIf all four sections printed OK, you are clear to write mutations.")
