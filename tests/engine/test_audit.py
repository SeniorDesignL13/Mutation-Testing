"""Whole audits on the example records, with stub judges (no network)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from mtlj.data.records import DatasetRecord
from mtlj.engine import audit
from mtlj.engine.audit import (
    EVALUATIONS_FILE,
    INTERRUPTED,
    check_run_dir,
    count_scorings,
    run_audit,
)
from mtlj.engine.generator import GenerationResult, default_battery, generate_mutants
from mtlj.engine.results import RESULTS_FILE, read_results
from mtlj.engine.runner import read_evaluations
from mtlj.judges.callable import CallableJudge

EXAMPLE_RECORDS = Path(__file__).parent.parent / "data" / "example_records.jsonl"
OFFSETS = [0.0, 0.02, -0.02, 0.01, -0.01]
# A run slow enough to stop part way, in its own process so the signal goes to a real main thread.
STOPPABLE_RUN = """
import sys, threading, time
from pathlib import Path
from mtlj.data.records import DatasetRecord
from mtlj.engine.audit import run_audit
from mtlj.engine.generator import GenerationResult
from mtlj.judges.callable import CallableJudge

records = [
    DatasetRecord.model_validate_json(line)
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
]
lock = threading.Lock()
calls = [0]

def score(input, output, context):
    with lock:
        calls[0] += 1
        if calls[0] == 12:  # At 4 calls at a time, some results are stored by now.
            print("started", flush=True)
    time.sleep(0.05)
    return 0.9

judge = CallableJudge(score, name="stub", model="m", version="1")
run_audit(
    GenerationResult(records, [], []), judge, Path(sys.argv[2]), dataset="test", samples=1000
)
"""


@pytest.fixture(scope="module")
def generation() -> GenerationResult:
    records = [
        DatasetRecord.model_validate_json(line)
        for line in EXAMPLE_RECORDS.read_text(encoding="utf-8").splitlines()
    ]
    return generate_mutants(records, default_battery(), base_seed=1)


def stub_judge(answers: list[str]) -> CallableJudge:
    """Scores an answer high while it still contains an original answer word for word."""
    calls: defaultdict[str, int] = defaultdict(int)
    lock = threading.Lock()

    def score(input, output, context):
        with lock:
            k = calls[output]
            calls[output] += 1
        base = 0.9 if any(answer in output for answer in answers) else 0.3
        return base + OFFSETS[k % len(OFFSETS)]

    return CallableJudge(score, name="stub", model="m", version="1")


def down_judge() -> CallableJudge:
    def score(input, output, context):
        raise ConnectionError("backend is down")

    return CallableJudge(score, name="stub", model="m", version="1")


def test_run_leaves_every_file_and_real_results(generation, tmp_path: Path):
    run_dir = tmp_path / "run"
    judge = stub_judge([r.answer for r in generation.originals])

    results = run_audit(generation, judge, run_dir, dataset="test", samples=5)

    names = {path.name for path in run_dir.iterdir()}
    assert names == {
        "originals.jsonl",
        "mutants.jsonl",
        "skipped.jsonl",
        "run.json",
        EVALUATIONS_FILE,
        RESULTS_FILE,
    }
    assert read_results(run_dir) == results
    assert results.run.status == "complete"
    assert results.run.samples == 5
    assert results.run.scorings.ok == count_scorings(generation, 5)
    swap = next(o for o in results.operators if o.operator == "entity_swap")
    assert swap.overall.rate == 1.0


def test_judge_going_down_leaves_an_aborted_results_file(generation, tmp_path: Path):
    run_dir = tmp_path / "run"

    results = run_audit(generation, down_judge(), run_dir, dataset="test", samples=5)

    assert results.run.status == "aborted"
    assert "in a row failed" in results.run.reason
    assert read_results(run_dir) == results
    rows = read_evaluations(run_dir / EVALUATIONS_FILE)
    assert len(rows) == results.run.scorings.total > 0
    assert results.run.scorings.errors_by_type == {"ConnectionError": len(rows)}


def test_ctrl_c_leaves_an_aborted_results_file(generation, tmp_path: Path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(audit, "run_judge_sync", interrupted)
    run_dir = tmp_path / "run"
    judge = stub_judge([r.answer for r in generation.originals])

    results = run_audit(generation, judge, run_dir, dataset="test", samples=5)

    assert results.run.status == "aborted"
    assert results.run.reason == INTERRUPTED
    assert results.run.scorings.total == 0
    assert read_results(run_dir) == results


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_a_stopped_process_leaves_an_aborted_results_file(tmp_path: Path, stop: signal.Signals):
    run_dir = tmp_path / "run"
    process = subprocess.Popen(
        [sys.executable, "-c", STOPPABLE_RUN, str(EXAMPLE_RECORDS), str(run_dir)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "started"

    process.send_signal(stop)

    assert process.wait(timeout=30) == 0
    results = read_results(run_dir)
    assert results.run.status == "aborted"
    assert results.run.reason == INTERRUPTED
    assert len(read_evaluations(run_dir / EVALUATIONS_FILE)) == results.run.scorings.total > 0


def test_stopping_handlers_are_put_back(generation, tmp_path: Path):
    before = signal.getsignal(signal.SIGTERM)

    run_audit(
        generation,
        stub_judge([r.answer for r in generation.originals]),
        tmp_path / "r",
        dataset="test",
    )

    assert signal.getsignal(signal.SIGTERM) is before


def test_a_run_that_cannot_start_leaves_the_directory_reusable(generation, tmp_path: Path):
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="samples"):
        run_audit(generation, down_judge(), run_dir, dataset="test", samples=0)

    check_run_dir(run_dir)


@pytest.mark.parametrize(
    "broken",
    [
        {"max_concurrency": 0},
        {"judge": CallableJudge(lambda *_: 1.0, version="")},
    ],
    ids=["concurrency", "judge"],
)
def test_every_rejected_start_leaves_the_directory_reusable(
    generation, tmp_path: Path, broken: dict
):
    run_dir = tmp_path / "run"
    args = {"judge": down_judge(), **broken}

    with pytest.raises(ValueError):
        run_audit(generation, args.pop("judge"), run_dir, dataset="test", **args)

    check_run_dir(run_dir)


def test_operator_with_no_family_is_rejected_before_any_scoring(generation, tmp_path: Path):
    broken = replace(generation, families={}, battery={"custom": [1.0]})
    calls = []
    judge = CallableJudge(lambda *_: calls.append(1) or 1.0)

    with pytest.raises(ValueError, match="no family recorded"):
        run_audit(broken, judge, tmp_path / "run", dataset="test")

    assert calls == []
    check_run_dir(tmp_path / "run")


def test_signal_while_saving_results_does_not_lose_them(generation, tmp_path: Path, monkeypatch):
    real_build = audit.build_results

    def stopped_while_building(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(audit, "build_results", stopped_while_building)
    run_dir = tmp_path / "run"
    judge = stub_judge([r.answer for r in generation.originals])

    results = run_audit(generation, judge, run_dir, dataset="test", samples=5)

    assert read_results(run_dir) == results
    assert results.run.status == "complete"


def test_nohup_keeps_working(generation, tmp_path: Path):
    seen = []
    good = stub_judge([r.answer for r in generation.originals])

    def score(input, output, context):
        seen.append(signal.getsignal(signal.SIGHUP))
        return good.score_raw(input, output, context)

    before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        run_audit(generation, CallableJudge(score), tmp_path / "run", dataset="test", samples=2)
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, before)

    assert set(seen) == {signal.SIG_IGN}


def test_handler_not_set_from_python_is_left_alone(generation, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audit.signal, "getsignal", lambda signum: None)
    judge = stub_judge([r.answer for r in generation.originals])

    results = run_audit(generation, judge, tmp_path / "run", dataset="test", samples=2)

    assert results.run.status == "complete"


def test_run_refuses_a_directory_in_use(generation, tmp_path: Path):
    (tmp_path / "other").write_text("someone else's run", encoding="utf-8")

    with pytest.raises(FileExistsError, match="isn't empty"):
        run_audit(generation, down_judge(), tmp_path, dataset="test")

    assert [path.name for path in tmp_path.iterdir()] == ["other"]


def test_an_empty_or_new_directory_is_accepted(tmp_path: Path):
    check_run_dir(tmp_path)
    check_run_dir(tmp_path / "new")


def test_a_file_is_not_accepted_as_a_run_directory(tmp_path: Path):
    path = tmp_path / "file"
    path.write_text("x", encoding="utf-8")

    with pytest.raises(FileExistsError):
        check_run_dir(path)


def test_count_scorings_counts_originals_and_mutants(generation):
    texts = len(generation.originals) + len(generation.mutants)

    assert count_scorings(generation, 5) == texts * 5
