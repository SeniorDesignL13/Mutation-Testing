"""Pilot-audit analysis for MTLJ-89: size the full study offline, from recorded scores.

Reproduce every number in docs/pilot-audits.md (no network, no API key), from the repo root:

    python -m uv run python scripts/pilot_analysis.py plan      # ~4 min: corpus-v1 scoring counts
    python -m uv run python scripts/pilot_analysis.py replay    # ~15 s: gold-set run dir, offline
    python -m uv run python scripts/pilot_analysis.py analyze   # seconds: latency, N, anomalies
    python -m uv run python scripts/pilot_analysis.py all
    python -m uv run python scripts/pilot_analysis.py live      # needs runs/pilot-* (below)

Stages:

* ``plan``: generates mutants for all 919 corpus-v1 records with the default battery, and with
  ``numeric_perturbation`` (0.2 / 0.6 / 1.0) on top, and counts planned scorings and skips.
  Writes ``docs/pilot/corpus_v1_generation.json``. Generation is local and calls no judge.
* ``replay``: replays ``tests/fixtures/judge/deepeval-faithfulness.jsonl`` through the real
  engine (``FixtureStore`` as the runner's cache), writing a full run directory to
  ``runs/pilot-gold-replay`` (git-ignored). Records wall time, peak memory and disk size in
  ``docs/pilot/replay_run.json``.
* ``analyze``: reads that run directory and writes ``docs/pilot/summary.json`` and
  ``docs/pilot/per_text.csv``: latency, extrapolation, N = 2/3/5 subsampling, saturation and
  anomaly evidence.

Latency is the ``latency_ms`` of the original recorded call, which a replayed evaluation keeps.

``live`` summarizes the live pilots made with scripts/pilot_live.py (needs OPENAI_API_KEY and
spends money; see docs/pilot-audits.md for the commands) into docs/pilot/live_summary.json.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import itertools
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "pilot"
RUN_DIR = ROOT / "runs" / "pilot-gold-replay"
FIXTURES = ROOT / "tests" / "fixtures"
SCORES = FIXTURES / "judge" / "deepeval-faithfulness.jsonl"
GOLD = FIXTURES / "gold-set"

NUMERIC_INTENSITIES = (0.2, 0.6, 1.0)
SAMPLES = 5
# README: "115 scorings ... under 15 cents with gpt-4o-mini". An upper bound, not a measurement.
README_COST_PER_SCORING = 0.15 / 115


# ---------------------------------------------------------------------------------- plan


def _generate_chunk(args):
    """Generate for one chunk of records in a worker. Returns counts only."""
    records, which = args
    from mtlj.engine.generator import BatteryItem, default_battery, generate_mutants
    from mtlj.operators.registry import get_operator

    if which == "numeric":
        battery = [BatteryItem(get_operator("numeric_perturbation"), NUMERIC_INTENSITIES)]
    else:
        battery = default_battery()
    gen = generate_mutants(records, battery, base_seed=0)
    muts = Counter((m.operator_name, m.intensity) for m in gen.mutants)
    skips = Counter((s.operator_name, s.intensity, s.reason) for s in gen.skipped)
    # Why an operator changed nothing, from the operator's own counters.
    no_change_why = Counter()
    for s in gen.skipped:
        if s.reason == "no_change":
            for key, value in s.detail.items():
                if isinstance(value, int) and value:
                    no_change_why[(s.operator_name, key)] += value
    swaps = Counter()
    for m in gen.mutants:
        for sub in m.details.get("substitutions", []):
            swaps[
                (m.operator_name, sub.get("label", "number"), sub.get("replacement_source", "-"))
            ] += 1
    replaced = Counter(
        sub["original"]
        for m in gen.mutants
        if m.operator_name == "entity_swap"
        for sub in m.details.get("substitutions", [])
    )
    bad_chars = sum("�" in r.answer for r in gen.originals)
    by_op_record = defaultdict(set)
    for m in gen.mutants:
        by_op_record[m.operator_name].add(m.record_id)
    return {
        "mutants": muts,
        "skips": skips,
        "no_change_why": no_change_why,
        "records": [r.record_id for r in gen.originals],
        "ops_with_mutant": {k: v for k, v in by_op_record.items()},
        "answer_words": [len(r.answer.split()) for r in gen.originals],
        "swaps": swaps,
        "replaced": replaced,
        "replacement_char_answers": bad_chars,
    }


def stage_plan() -> None:
    from mtlj.data.ragtruth import load_corpus_v1

    records = load_corpus_v1().records
    chunks = [records[i : i + 40] for i in range(0, len(records), 40)]
    out = {"n_records": len(records), "samples": SAMPLES}
    for which in ("default", "numeric"):
        start = time.perf_counter()
        with ProcessPoolExecutor(max_workers=4) as pool:
            parts = list(pool.map(_generate_chunk, [(c, which) for c in chunks]))
        elapsed = time.perf_counter() - start
        mutants, skips, why = Counter(), Counter(), Counter()
        swaps, replaced, bad_chars = Counter(), Counter(), 0
        ops_with = defaultdict(set)
        words = []
        for p in parts:
            mutants.update(p["mutants"])
            skips.update(p["skips"])
            why.update(p["no_change_why"])
            words += p["answer_words"]
            swaps.update(p["swaps"])
            replaced.update(p["replaced"])
            bad_chars += p["replacement_char_answers"]
            for op, ids in p["ops_with_mutant"].items():
                ops_with[op] |= ids
        out[which] = {
            "generation_seconds": round(elapsed, 1),
            "mutants": {f"{op}@{i}": n for (op, i), n in sorted(mutants.items())},
            "mutants_total": sum(mutants.values()),
            "skips": {f"{op}@{i}:{r}": n for (op, i, r), n in sorted(skips.items())},
            "no_change_why": {f"{op}:{k}": n for (op, k), n in sorted(why.items())},
            "records_with_a_mutant": {op: len(ids) for op, ids in sorted(ops_with.items())},
            "scorings_with_originals": SAMPLES * (len(records) + sum(mutants.values())),
            "substitutions_by_op_label_source": {
                f"{op}|{label}|{src}": n for (op, label, src), n in sorted(swaps.items())
            },
            "most_replaced_tokens": dict(replaced.most_common(25)),
            "answers_with_replacement_char": bad_chars,
        }
        out["answer_words_mean"] = statistics.fmean(words)
        out["answer_words_median"] = statistics.median(words)
        print(
            which,
            out[which]["mutants_total"],
            out[which]["scorings_with_originals"],
            f"{elapsed:.0f}s",
        )
    out["default_plus_numeric_scorings"] = SAMPLES * (
        len(records) + out["default"]["mutants_total"] + out["numeric"]["mutants_total"]
    )
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "corpus_v1_generation.json").write_text(json.dumps(out, indent=2) + "\n")


# --------------------------------------------------------------------------------- replay


def _peak_working_set_mb() -> float | None:
    """Peak working set of this process in MB (Windows only; None elsewhere)."""
    if sys.platform != "win32":
        try:
            import resource

            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except ImportError:
            return None

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    handle = ctypes.c_void_p(kernel.GetCurrentProcess())
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return counters.PeakWorkingSetSize / 1e6 if ok else None


def _dir_bytes(path: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in sorted(path.iterdir()) if p.is_file()}


def stage_replay() -> None:
    from mtlj.engine.audit import EVALUATIONS_FILE
    from mtlj.engine.fixtures import FixtureStore, load_inputs
    from mtlj.engine.generator import default_battery, generate_mutants, write_generation
    from mtlj.engine.results import build_results, format_summary, write_results
    from mtlj.engine.runner import JsonlSink, read_evaluations, run_judge_sync
    from mtlj.judges.deepeval.faithfulness import DeepEvalFaithfulnessJudge

    if RUN_DIR.exists() and any(RUN_DIR.iterdir()):
        sys.exit(f"{RUN_DIR} is not empty; delete it to replay again")

    records, mutants = load_inputs(GOLD)
    # The skipped list isn't saved with the fixtures, so make it again. The saved mutants are
    # what was scored; the check proves today's operators still make the same ones.
    generation = generate_mutants(records, default_battery(), base_seed=0)
    assert {m.mutant_id for m in generation.mutants} == {m.mutant_id for m in mutants}
    saved = {m.mutant_id: m for m in mutants}
    generation.mutants = [saved[m.mutant_id] for m in generation.mutants]

    def refuse(input, output, passages):
        raise AssertionError("replay must not call the judge")

    store = FixtureStore(SCORES, mode="replay")
    recorded = store.judge
    judge = store.replay_as(
        DeepEvalFaithfulnessJudge(recorded.model, **recorded.settings, measure=refuse)
    )

    write_generation(generation, RUN_DIR)
    start = time.perf_counter()
    with JsonlSink(RUN_DIR / EVALUATIONS_FILE) as sink:
        summary = run_judge_sync(
            judge, generation.originals, generation.mutants, sink, cache=store, samples=SAMPLES
        )
    replay_seconds = time.perf_counter() - start
    results = build_results(
        generation,
        read_evaluations(RUN_DIR / EVALUATIONS_FILE),
        judge=judge,
        dataset="gold",
        samples=SAMPLES,
    )
    write_results(results, RUN_DIR)
    sizes = _dir_bytes(RUN_DIR)
    info = {
        "scorings": summary.total,
        "ok": summary.ok,
        "errors": summary.errors,
        "cache_hits": summary.cache_hits,
        "replay_wall_seconds": round(replay_seconds, 2),
        "peak_working_set_mb": _peak_working_set_mb(),
        "run_dir_bytes": sizes,
        "run_dir_total_bytes": sum(sizes.values()),
        "evaluations_bytes_per_scoring": sizes[EVALUATIONS_FILE] / summary.total,
    }
    (DOCS).mkdir(parents=True, exist_ok=True)
    (DOCS / "replay_run.json").write_text(json.dumps(info, indent=2) + "\n")
    print(format_summary(results))
    print(json.dumps(info, indent=2))


# -------------------------------------------------------------------------------- analyze


def _pct(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _latency(values_ms):
    s = [v / 1000 for v in values_ms]
    return {
        "n": len(s),
        "mean_s": round(statistics.fmean(s), 2),
        "median_s": round(statistics.median(s), 2),
        "p90_s": round(_pct(s, 0.9), 2),
        "p99_s": round(_pct(s, 0.99), 2),
        "max_s": round(max(s), 2),
    }


def _load(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _verdicts_at(originals, mutants_by_group, subset, alpha=0.05):
    """Detection / spurious counts per operator when only the samples in ``subset`` are used."""
    from mtlj.stats.detection import MutantScores, detect_mutant, noise_floor
    from mtlj.stats.spurious import flag_mutant

    def pick(xs):
        return [xs[i] for i in subset]

    orig = {rid: pick(s) for rid, s in originals.items()}
    sigma = noise_floor(orig.values())
    out = {}
    for (op, family), muts in mutants_by_group.items():
        hits = testable = 0
        verdicts = {}
        for mid, rid, changed_scores in muts:
            ms = MutantScores(rid, pick(changed_scores))
            if family == "degrading":
                o = detect_mutant(orig[rid], ms, sigma, alpha)
            else:
                o = flag_mutant(orig[rid], ms, sigma, alpha)
            if o.excluded is None:
                testable += 1
                hits += o.detected
                verdicts[mid] = o.detected
        out[op] = (hits, testable, verdicts)
    return sigma, out


def stage_analyze() -> None:
    from mtlj.engine.generator import default_battery  # noqa: F401  (import check)

    evals = _load(RUN_DIR / "evaluations.jsonl")
    mutants = {m["mutant_id"]: m for m in _load(RUN_DIR / "mutants.jsonl")}
    skipped = _load(RUN_DIR / "skipped.jsonl")
    results = json.loads((RUN_DIR / "results.json").read_text(encoding="utf-8"))
    replay = json.loads((DOCS / "replay_run.json").read_text())
    gen = json.loads((DOCS / "corpus_v1_generation.json").read_text())
    summary: dict = {}

    # ---- 1. latency
    by_group = defaultdict(list)
    for e in evals:
        by_group["originals" if e["kind"] == "original" else "mutants"].append(e["latency_ms"])
        by_group["all"].append(e["latency_ms"])
        if e["kind"] == "mutant":
            by_group[e["operator_name"]].append(e["latency_ms"])
    summary["latency"] = {k: _latency(v) for k, v in by_group.items()}
    by_int = defaultdict(list)
    for e in evals:
        if e["kind"] == "mutant":
            by_int[f"{e['operator_name']}@{e['intensity']}"].append(e["latency_ms"])
    summary["latency_by_intensity"] = {k: _latency(v) for k, v in sorted(by_int.items())}
    # Latency against the answer's claim count (more claims = more verdicts to write).
    pairs = [(e["n_claims"], e["latency_ms"] / 1000) for e in evals if e["n_claims"]]
    xs, ys = zip(*pairs, strict=True)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    r = sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    summary["latency_claims_corr"] = round(r, 3)
    by_rec = defaultdict(list)
    for e in evals:
        if e["kind"] == "original":
            by_rec[e["record_id"]].append(e["latency_ms"] / 1000)
    summary["latency_slowest_records"] = sorted(
        ((statistics.fmean(v), rid) for rid, v in by_rec.items()), reverse=True
    )[:3]

    # ---- extrapolation (per-operator mean latency x planned scorings)
    mean_s = {k: statistics.fmean(v) / 1000 for k, v in by_group.items()}
    ops = defaultdict(int)  # operator -> planned scorings in the full study
    for which in ("default", "numeric"):
        for key, n in gen[which]["mutants"].items():
            ops[key.split("@")[0]] += n * SAMPLES
    ops["original"] = gen["n_records"] * SAMPLES
    # Numeric perturbation has no recorded latency: use entity_swap's (the closest operator).
    proxy = {
        **mean_s,
        "original": mean_s["originals"],
        "numeric_perturbation": mean_s["entity_swap"],
        # No gold record is a list, so reformat_to_prose has no recorded latency (6 corpus mutants).
        "reformat_to_prose": mean_s["reformat_to_bullets"],
    }
    ext = {}
    for label, names in (
        ("default", [o for o in ops if o != "numeric_perturbation"]),
        ("default+numeric", list(ops)),
    ):
        total_scorings = sum(ops[o] for o in names)
        sec = sum(ops[o] * proxy[o] for o in names)
        ext[label] = {
            "scorings": total_scorings,
            "mean_latency_s": round(sec / total_scorings, 2),
            "hours_c4": round(sec / 4 / 3600, 1),
            "hours_c8": round(sec / 8 / 3600, 1),
            "dollars_upper_bound": round(total_scorings * README_COST_PER_SCORING, 2),
        }
    summary["extrapolation_per_config"] = ext
    summary["cost_per_scoring_upper_bound"] = README_COST_PER_SCORING
    # Cross-check from the README ("115 scorings, about 4 minutes" at the default 4 concurrent)
    # and the spike (400 scorings in 6.7 min at 4 concurrent).
    summary["throughput_cross_checks"] = {
        "readme_s_per_scoring_at_c4": 4 * 60 / 115,
        "spike_s_per_scoring_at_c4": 6.7 * 60 / 400,
        "recorded_wall_s_per_scoring_at_c4": mean_s["all"] / 4,
    }

    # ---- disk and memory
    summary["disk"] = {
        "run_dir_bytes": replay["run_dir_total_bytes"],
        "kb_per_1000_scorings": round(
            replay["run_dir_total_bytes"] / replay["scorings"] * 1000 / 1024, 1
        ),
        "evaluations_kb_per_1000": round(replay["evaluations_bytes_per_scoring"] * 1000 / 1024, 1),
        "files": replay["run_dir_bytes"],
    }
    summary["memory_peak_mb_replay"] = replay["peak_working_set_mb"]

    # ---- 2. sample count
    originals = defaultdict(list)
    mscores = defaultdict(list)
    for e in sorted(evals, key=lambda e: e["sample_index"]):
        if e["status"] != "ok":
            continue
        (originals[e["record_id"]] if e["kind"] == "original" else mscores[e["target_id"]]).append(
            e["score"]
        )
    from mtlj.stats.detection import noise_floor

    sat = [rid for rid, s in originals.items() if len(set(s)) == 1]
    sat_one = [rid for rid in sat if originals[rid][0] == 1.0]
    summary["saturation"] = {
        "originals": len(originals),
        "identical_all_samples": len(sat),
        "identical_at_1.0": len(sat_one),
        "varied": len(originals) - len(sat),
        "mutants": len(mscores),
        "mutants_identical_all_samples": sum(len(set(s)) == 1 for s in mscores.values()),
        "originals_mean_ge_0.95": sum(statistics.fmean(s) >= 0.95 for s in originals.values()),
        "var_share_top_original": None,
    }
    var = {rid: statistics.variance(s) for rid, s in originals.items()}
    top = max(var, key=var.get)
    summary["saturation"]["var_share_top_original"] = [top, round(var[top] / sum(var.values()), 3)]
    sigma5 = noise_floor(originals.values())
    summary["sigma_n5"] = sigma5
    summary["sigma_results_json"] = results["noise"]["sigma"]

    from statistics import NormalDist

    z1, z2 = NormalDist().inv_cdf(0.95), NormalDist().inv_cdf(0.975)
    summary["tau"] = {
        str(n): {
            "tau_degrading": z1 * sigma5 * math.sqrt(2 / n),
            "tau_preserving": z2 * sigma5 * math.sqrt(2 / n),
            "ratio_degrading": z1 * math.sqrt(2 / n),
        }
        for n in (2, 3, 5, 10)
    }

    groups = defaultdict(list)
    for mid, m in mutants.items():
        groups[(m["operator_name"], m["operator_family"])].append(
            (mid, m["record_id"], mscores[mid])
        )
    # Order records by id so sample indexes are consistent per text (they are: sorted above).
    sub = {}
    for k in (2, 3, 4, 5):
        runs = []
        for subset in itertools.combinations(range(SAMPLES), k):
            sigma, per_op = _verdicts_at(originals, groups, subset)
            runs.append((sigma, per_op))
        sub[k] = runs
    _, base_ops = sub[5][0]
    table = {}
    for k, runs in sub.items():
        row = {
            "sigma_mean": statistics.fmean(s for s, _ in runs),
            "sigma_min": min(s for s, _ in runs),
            "sigma_max": max(s for s, _ in runs),
            "n_subsets": len(runs),
            "operators": {},
        }
        for op in base_ops:
            rates = [h / t if t else float("nan") for _, per in runs for h, t, _ in [per[op]]]
            hits = [per[op][0] for _, per in runs]
            testable = [per[op][1] for _, per in runs]
            agree = []
            for _, per in runs:
                v = per[op][2]
                b = base_ops[op][2]
                common = [mid for mid in b if mid in v]
                agree.append(
                    sum(v[m] == b[m] for m in common) / len(common) if common else float("nan")
                )
            row["operators"][op] = {
                "rate_mean": statistics.fmean(rates),
                "rate_min": min(rates),
                "rate_max": max(rates),
                "hits_mean": statistics.fmean(hits),
                "testable_mean": statistics.fmean(testable),
                "agreement_with_n5": statistics.fmean(agree),
            }
        table[str(k)] = row
    summary["subsample"] = table

    # Spike's gold-set entity swaps: how many 13 of 20 vs. our automated (for reference only).
    # ---- 3. anomalies
    gold_ids = [r["record_id"] for r in _load(RUN_DIR / "originals.jsonl")]
    skip_counts = Counter((s["operator_name"], s["reason"]) for s in skipped)
    summary["skips"] = {f"{o}:{r}": n for (o, r), n in sorted(skip_counts.items())}
    sw_records = {m["record_id"] for m in mutants.values() if m["operator_name"] == "entity_swap"}
    summary["entity_swap_records_with_mutant"] = [len(sw_records), len(gold_ids)]
    swaps = []
    for m in mutants.values():
        if m["operator_name"] != "entity_swap":
            continue
        for sub_ in m["details"].get("substitutions", []):
            swaps.append({"record": m["record_id"], "intensity": m["intensity"], **sub_})
    summary["entity_swaps"] = swaps
    from mtlj.stats.detection import MutantScores, detect_mutant

    per_swap = []
    for mid, m in mutants.items():
        if m["operator_name"] == "entity_swap":
            o = detect_mutant(
                originals[m["record_id"]], MutantScores(m["record_id"], mscores[mid]), sigma5
            )
            subs = m["details"]["substitutions"]
            per_swap.append(
                {
                    "record": m["record_id"],
                    "intensity": m["intensity"],
                    "swaps": [
                        f"{x['original']} -> {x['replacement']} "
                        f"({x['label']}, {x['replacement_source']})"
                        for x in subs
                    ],
                    "delta": None if o.delta is None else round(o.delta, 3),
                    "detected": o.detected,
                    "excluded": o.excluded.value if o.excluded else None,
                    "orig_mean": round(statistics.fmean(originals[m["record_id"]]), 3),
                    "mut_scores": mscores[mid],
                }
            )
    summary["entity_swap_outcomes"] = per_swap
    summary["errors"] = {
        "errors": results["run"]["scorings"]["errors"],
        "by_type": results["run"]["scorings"]["errors_by_type"],
    }
    summary["attempts_gt1"] = sum(e["attempts"] != 1 for e in evals if not e["cached"])
    summary["cached"] = sum(e["cached"] for e in evals)
    # Verdict counts: do borderline verdicts hide misses (score unchanged, borderline up)?
    summary["operators"] = [
        {
            "operator": o["operator"],
            "family": o["family"],
            "repeated_doses": o["repeated_doses"],
            "overall": {
                k: o["overall"].get(k)
                for k in (
                    "rate",
                    "testable",
                    "excluded",
                    "detected",
                    "flagged",
                    "penalized",
                    "rewarded",
                    "tau",
                )
            },
            "by_intensity": [
                {
                    "intensity": b["intensity"],
                    "repeated_doses": b["repeated_doses"],
                    **{
                        k: b["result"].get(k)
                        for k in (
                            "rate",
                            "testable",
                            "excluded",
                            "detected",
                            "flagged",
                            "penalized",
                            "rewarded",
                            "above_chance_corrected",
                            "p_adjusted",
                        )
                    },
                }
                for b in o["by_intensity"]
            ],
        }
        for o in results["operators"]
    ]
    summary["noise_tau_results_json"] = {k: results["noise"][k] for k in ("sigma", "n_originals")}
    summary["correction"] = results["correction"]

    # per-original verdict mix
    per_text = []
    for rid in gold_ids:
        s = originals[rid]
        yes = [e for e in evals if e["kind"] == "original" and e["record_id"] == rid]
        per_text.append(
            {
                "record": rid,
                "mean": statistics.fmean(s),
                "min": min(s),
                "max": max(s),
                "distinct": len(set(s)),
                "n_claims_mean": statistics.fmean(e["n_claims"] for e in yes),
                "no_verdicts": sum(e["verdict_no"] for e in yes),
                "borderline_verdicts": sum(e["verdict_borderline"] for e in yes),
                "latency_mean_s": statistics.fmean(e["latency_ms"] for e in yes) / 1000,
            }
        )
    with (DOCS / "per_text.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_text[0]))
        w.writeheader()
        for row in per_text:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()})
    (DOCS / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "latency",
                    "extrapolation_per_config",
                    "saturation",
                    "sigma_n5",
                    "tau",
                    "skips",
                )
            },
            indent=1,
            default=str,
        )
    )


LIVE_RUNS = {
    "default": "pilot-default",
    "penalized": "pilot-penalized",
    "c8": "pilot-c8",
    "ragas": "pilot-ragas",
}


def stage_live() -> None:
    """Summarize the live pilots in runs/ (made by scripts/pilot_live.py) next to the replay."""
    gen = json.loads((DOCS / "corpus_v1_generation.json").read_text())
    out: dict = {}
    for label, name in LIVE_RUNS.items():
        directory = ROOT / "runs" / name
        if not (directory / "cost.json").exists():
            continue
        evals = _load(directory / "evaluations.jsonl")
        cost = json.loads((directory / "cost.json").read_text())
        results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
        ok = [e for e in evals if e["status"] == "ok"]
        scores = defaultdict(list)
        for e in ok:
            scores[e["target_id"] if e["kind"] == "mutant" else e["record_id"]].append(e["score"])
        orig_ids = {e["record_id"] for e in evals if e["kind"] == "original"}
        orig = {r: scores[r] for r in orig_ids}
        mut = {t: s for t, s in scores.items() if t not in orig_ids}
        retries = Counter()
        for e in evals:
            for r in e["retry_errors"]:
                retries[r] += 1
        row = {
            "scorings": len(evals),
            "ok": len(ok),
            "errors": results["run"]["scorings"]["errors"],
            "errors_by_type": results["run"]["scorings"]["errors_by_type"],
            "scorings_with_retry": sum(e["attempts"] > 1 for e in evals),
            "retries_by_error": dict(retries),
            "latency": {
                "all": _latency([e["latency_ms"] for e in ok]),
                "originals": _latency([e["latency_ms"] for e in ok if e["kind"] == "original"]),
                "mutants": _latency([e["latency_ms"] for e in ok if e["kind"] == "mutant"]),
            },
            "wall_seconds": cost["wall_seconds"],
            "wall_seconds_per_scoring": cost["wall_seconds"] / len(evals),
            "concurrency": cost["concurrency"],
            "spent_usd": cost["spent_usd"],
            "priced_scorings": cost["priced_scorings"],
            "unpriced_scorings": cost["unpriced_scorings"],
            "usd_per_scoring": cost["spent_usd"] / max(cost["priced_scorings"], 1),
            "llm_calls": cost["llm_calls"],
            "llm_calls_per_scoring": cost["llm_calls"] / len(evals),
            "input_tokens_per_scoring": cost["input_tokens"] / len(evals),
            "output_tokens_per_scoring": cost["output_tokens"] / len(evals),
            "sigma": results["noise"]["sigma"] if results["noise"] else None,
            "originals": len(orig),
            "originals_identical": sum(len(set(s)) == 1 for s in orig.values()),
            "originals_identical_at_1": sum(
                len(set(s)) == 1 and s[0] == 1.0 for s in orig.values()
            ),
            "mutants_identical": sum(len(set(s)) == 1 for s in mut.values()),
            "mutants": len(mut),
            "orig_mean_score": statistics.fmean(s for v in orig.values() for s in v),
            "operators": {
                o["operator"]: {
                    "family": o["family"],
                    "rate": o["overall"]["rate"],
                    "testable": o["overall"]["testable"],
                    "hits": o["overall"].get("detected", o["overall"].get("flagged")),
                    "excluded": o["overall"]["excluded"],
                    "repeated_doses": o["repeated_doses"],
                    "above_chance_corrected": [
                        b["result"].get("above_chance_corrected") for b in o["by_intensity"]
                    ],
                }
                for o in results["operators"]
            },
        }
        out[label] = row
    # Full-study extrapolation from the measured mean latency and cost (default battery).
    full = gen["default"]["scorings_with_originals"]
    plus = gen["default_plus_numeric_scorings"]
    for label in ("default", "penalized", "ragas"):
        if label in out:
            r = out[label]
            mean = r["latency"]["all"]["mean_s"]
            r["extrapolation"] = {
                "scorings": full,
                "hours_c4": full * mean / 4 / 3600,
                "hours_c8": full * mean / 8 / 3600,
                "usd": full * r["usd_per_scoring"],
                "scorings_plus_numeric": plus,
                "usd_plus_numeric": plus * r["usd_per_scoring"],
                "hours_c4_plus_numeric": plus * mean / 4 / 3600,
                "hours_c8_plus_numeric": plus * mean / 8 / 3600,
            }
    (DOCS / "live_summary.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("stage", choices=["plan", "replay", "analyze", "live", "all"])
    stage = parser.parse_args().stage
    if stage == "live":
        stage_live()
    if stage in ("plan", "all"):
        stage_plan()
    if stage in ("replay", "all"):
        stage_replay()
    if stage in ("analyze", "all"):
        stage_analyze()


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
