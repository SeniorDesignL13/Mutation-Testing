"""Tests for the async judge runner."""

import asyncio
import itertools
import json
import logging
import math
import pickle
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine import runner
from mtlj.engine.cache import CacheKeys, ResponseCache
from mtlj.engine.generator import default_battery, generate_mutants
from mtlj.engine.ratelimit import RateLimiter
from mtlj.engine.retry import NO_RETRY, RetryPolicy
from mtlj.engine.runner import (
    DEFAULT_MAX_CONCURRENCY,
    ERROR,
    MUTANT,
    NO_RATE_LIMIT,
    OK,
    ORIGINAL,
    Evaluation,
    JsonlSink,
    MemorySink,
    RunAbortedError,
    RunLimits,
    RunSummary,
    build_tasks,
    judge_context,
    read_evaluations,
    resolve_limits,
    run_judge,
    run_judge_sync,
)
from mtlj.judges.base import Judge, RawScore, VerdictCounts
from mtlj.judges.callable import CallableJudge
from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)
from mtlj.stats.detection import MutantScores, detection_rate

EXAMPLE_RECORDS = Path(__file__).parent.parent / "data" / "example_records.jsonl"


def _record(n: int) -> DatasetRecord:
    return DatasetRecord(
        record_id=f"t_{n}",
        source_dataset="test",
        question=f"Where was conference {n} held?",
        context=[
            {"passage_id": "p1", "text": "The annual conference took place in Paris."},
            {"passage_id": "p2", "text": f"Conference {n} had many visitors."},
        ],
        answer=f"The conference number {n} was held in Paris last spring.",
    )


class Shout(MutationOperator):
    """Appends '!!!', so every mutant differs from its original."""

    metadata = OperatorMetadata(
        name="shout",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description="test operator",
    )

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        return MutationResult(text, text + "!!!", True, {})


RECORDS = [_record(n) for n in range(3)]
MUTANTS = [Mutant.generate(r, Shout(), 1.0, seed=7) for r in RECORDS]


def judge(score_fn=lambda i, o, c: 0.5, **kwargs) -> CallableJudge:
    return CallableJudge(score_fn, name="stub", model="stub-model", version="1.0", **kwargs)


async def run(score_fn=lambda i, o, c: 0.5, *, samples=2, sink=None, retry=NO_RETRY, **kwargs):
    # Without retries, so every failure here is final. tests/engine/test_retry.py has those.
    sink = sink if sink is not None else MemorySink()
    summary = await run_judge(
        judge(score_fn), RECORDS, MUTANTS, sink, samples=samples, retry=retry, **kwargs
    )
    return summary, sink


def always_fails(input, output, context):
    raise PermissionError("invalid API key")


# --------------------------------------------------
# Building the tasks
# --------------------------------------------------


def test_every_original_and_mutant_is_scored_samples_times() -> None:
    tasks = build_tasks(RECORDS, MUTANTS, samples=3)

    assert len(tasks) == len(list(tasks)) == 3 * (len(RECORDS) + len(MUTANTS))
    counts = Counter((t.kind, t.target_id) for t in tasks)
    assert len(counts) == len(RECORDS) + len(MUTANTS)
    assert set(counts.values()) == {3}
    assert {(t.target_id, t.sample_index) for t in tasks} == {
        (target_id, i) for _, target_id in counts for i in range(3)
    }


def test_tasks_go_sample_by_sample() -> None:
    tasks = build_tasks(RECORDS, MUTANTS, samples=3)

    assert [t.sample_index for t in tasks] == sorted(t.sample_index for t in tasks)


def test_tasks_carry_what_the_judge_needs_and_the_mutants_provenance() -> None:
    tasks = build_tasks(RECORDS[:1], MUTANTS[:1], samples=1)
    original, mutant = tasks

    assert (original.kind, original.target_id, original.operator_name) == (
        ORIGINAL,
        "t_0",
        None,
    )
    assert original.output == RECORDS[0].answer
    assert (mutant.kind, mutant.target_id, mutant.record_id) == (
        MUTANT,
        MUTANTS[0].mutant_id,
        "t_0",
    )
    assert (mutant.operator_name, mutant.intensity) == ("shout", 1.0)
    assert mutant.output == MUTANTS[0].mutated_text
    for task in tasks:
        assert task.input == RECORDS[0].question
        assert task.passages == tuple(RECORDS[0].context)


def _with_answer(record: DatasetRecord, answer: str) -> DatasetRecord:
    return DatasetRecord.model_validate(record.model_dump() | {"answer": answer})


def _with_passage(record: DatasetRecord, text: str) -> DatasetRecord:
    passages = [{"passage_id": "p9", "text": text}]
    return DatasetRecord.model_validate(record.model_dump() | {"context": passages})


@pytest.mark.parametrize(
    ("records", "mutants", "samples", "message"),
    [
        (RECORDS, MUTANTS, 0, "samples must be at least 1"),
        ([*RECORDS, RECORDS[0]], MUTANTS, 1, "duplicate record_id"),
        (RECORDS, [*MUTANTS, MUTANTS[0]], 1, "duplicate mutant_id"),
        (RECORDS[1:], MUTANTS, 1, "is missing"),
        ([_with_answer(RECORDS[0], "New answer."), *RECORDS[1:]], MUTANTS, 1, "answer has"),
        ([_with_passage(RECORDS[0], "New passage."), *RECORDS[1:]], MUTANTS, 1, "context has"),
    ],
)
def test_bad_input_is_rejected_before_anything_runs(records, mutants, samples, message) -> None:
    with pytest.raises(ValueError, match=message):
        build_tasks(records, mutants, samples)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_concurrency": 0}, "max_concurrency"),
        ({"max_consecutive_errors": 0}, "max_consecutive_errors"),
    ],
)
async def test_bad_limits_are_rejected(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        await run(**kwargs)


@pytest.mark.parametrize(("attr", "value"), [("name", ""), ("model", None), ("version", 2)])
async def test_a_judge_with_bad_identity_is_rejected_before_any_call(attr, value) -> None:
    calls = 0

    def score(input, output, context):
        nonlocal calls
        calls += 1
        return 0.5

    bad = judge(score)
    setattr(bad, attr, value)
    with pytest.raises(ValueError, match=f"judge.{attr} must be a non-empty string"):
        await run_judge(bad, RECORDS, MUTANTS, MemorySink())
    assert calls == 0


async def test_no_records_gives_an_empty_summary() -> None:
    sink = MemorySink()
    summary = await run_judge(judge(), [], [], sink)

    assert (summary.total, summary.ok, summary.errors) == (0, 0, 0)
    assert sink.evaluations == []


# --------------------------------------------------
# Scoring
# --------------------------------------------------


async def test_judge_is_given_the_question_the_answer_and_the_passages() -> None:
    seen = []

    def score(input, output, context):
        seen.append((input, output, context))
        return 0.5

    await run(score, samples=1)

    paris = "The annual conference took place in Paris."
    context = {
        "passages": [
            {"passage_id": "p1", "text": paris, "title": None},
            {"passage_id": "p2", "text": "Conference 0 had many visitors.", "title": None},
        ]
    }
    assert (RECORDS[0].question, RECORDS[0].answer, context) in seen
    assert (RECORDS[0].question, MUTANTS[0].mutated_text, context) in seen


def test_judge_context_has_the_shape_the_adapter_contract_tests() -> None:
    # tests/judges/test_contract.py feeds adapters passages with exactly these keys.
    context = judge_context(RECORDS[0].context)

    assert set(context) == {"passages"}
    assert all(set(p) == {"passage_id", "text", "title"} for p in context["passages"])
    assert judge_context(RECORDS[0].context)["passages"] is not context["passages"]


async def test_every_sample_is_stored_with_the_judges_identity() -> None:
    summary, sink = await run(samples=3)

    assert summary.total == summary.ok == len(sink.evaluations) == 3 * 6
    assert summary.errors == 0
    assert (summary.judge_name, summary.model, summary.version, summary.samples) == (
        "stub",
        "stub-model",
        "1.0",
        3,
    )
    for e in sink.evaluations:
        assert (e.status, e.score, e.raw_score) == (OK, 0.5, 0.5)
        assert (e.judge_name, e.model, e.version) == ("stub", "stub-model", "1.0")
        assert e.error_type is e.error is None
        assert e.latency_ms >= 0
    keys = Counter((e.target_id, e.sample_index) for e in sink.evaluations)
    assert set(keys.values()) == {1}


async def test_verdict_counts_and_n_claims_are_kept() -> None:
    raw = RawScore(0.75, verdict_counts=VerdictCounts(yes=5, borderline=1, no=1), n_claims=8)
    _, sink = await run(lambda i, o, c: raw, samples=1)

    for e in sink.evaluations:
        assert (e.verdict_yes, e.verdict_borderline, e.verdict_no, e.n_claims) == (5, 1, 1, 8)


async def test_a_plain_score_has_no_verdict_counts() -> None:
    _, sink = await run(samples=1)

    for e in sink.evaluations:
        assert e.verdict_yes is e.verdict_borderline is e.verdict_no is e.n_claims is None


async def test_scores_are_normalized_and_raw_scores_kept() -> None:
    sink = MemorySink()
    await run_judge(
        judge(lambda i, o, c: 4.0, score_range=(1.0, 5.0)), RECORDS, MUTANTS, sink, samples=1
    )

    assert {(e.score, e.raw_score) for e in sink.evaluations} == {(0.75, 4.0)}


# --------------------------------------------------
# Concurrency
# --------------------------------------------------


async def test_calls_run_at_the_same_time() -> None:
    # Each call waits until 4 calls are in flight, so the run only succeeds if they overlap.
    barrier = threading.Barrier(4)

    def score(input, output, context):
        barrier.wait(timeout=5)
        return 0.5

    summary, _ = await run(score, samples=2, max_concurrency=4)  # 12 calls, a multiple of 4

    assert summary.ok == 12


async def test_never_more_than_max_concurrency_calls_in_flight() -> None:
    # The barrier makes every call wait for 3 to be in flight, so the peak is exactly 3 unless
    # the runner lets a 4th in, whatever the machine's timing.
    barrier = threading.Barrier(3)
    lock = threading.Lock()
    in_flight = peak = 0

    def score(input, output, context):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        barrier.wait(timeout=5)
        with lock:
            in_flight -= 1
        return 0.5

    summary, _ = await run(score, samples=4, max_concurrency=3)

    assert summary.ok == 24
    assert peak == 3


async def test_results_are_written_as_they_complete() -> None:
    # One call can only finish after the sink has received another call's result.
    first_written = threading.Event()
    slow_output = MUTANTS[0].mutated_text

    class SignallingSink(MemorySink):
        async def write(self, evaluation: Evaluation) -> None:
            await super().write(evaluation)
            first_written.set()

    def score(input, output, context):
        if output == slow_output and not first_written.wait(timeout=5):
            raise TimeoutError("nothing was written while this call ran")
        return 0.5

    summary, _ = await run(score, samples=1, sink=SignallingSink())

    assert summary.errors == 0


# --------------------------------------------------
# Failures
# --------------------------------------------------


async def test_failed_calls_are_recorded_and_the_run_carries_on(caplog) -> None:
    bad_mutant = MUTANTS[1]

    def score(input, output, context):
        if output == bad_mutant.mutated_text:
            raise ConnectionError("backend is down")
        if output == RECORDS[2].answer:
            return 7.0  # out of range: Judge.__call__ raises ValueError
        return 0.5

    with caplog.at_level(logging.WARNING, logger="mtlj.engine.runner"):
        summary, sink = await run(score, samples=2)

    assert summary.total == 12
    assert summary.ok == 8
    assert summary.errors == 4
    assert summary.errors_by_type == {"ConnectionError": 2, "ValueError": 2}

    failed = [e for e in sink.evaluations if e.status == ERROR]
    assert {e.target_id for e in failed} == {bad_mutant.mutant_id, "t_2"}
    for e in failed:
        assert e.score is e.raw_score is None
        assert e.verdict_yes is e.n_claims is None
        assert (e.judge_name, e.model, e.version) == ("stub", "stub-model", "1.0")
    down = next(e for e in failed if e.target_id == bad_mutant.mutant_id)
    assert (down.error_type, down.error) == ("ConnectionError", "backend is down")
    assert "backend is down" in caplog.text


async def test_a_failing_sink_stops_the_run_and_is_not_written_to_again() -> None:
    calls = 0
    lock = threading.Lock()

    class FullDisk(MemorySink):
        """Fails on the third write; counts any write the runner tries after that."""

        failed = False
        writes_after_failure = 0

        async def write(self, evaluation: Evaluation) -> None:
            if self.failed:
                self.writes_after_failure += 1
            elif len(self.evaluations) == 2:
                self.failed = True
                raise OSError("disk full")
            await super().write(evaluation)

    def score(input, output, context):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.005)
        return 0.5

    before = asyncio.all_tasks()
    sink = FullDisk()
    with pytest.raises(OSError, match="disk full") as excinfo:
        await run(score, samples=5, max_concurrency=4, sink=sink)

    assert asyncio.all_tasks() == before
    assert sink.writes_after_failure == 0
    assert len(sink.evaluations) == 2
    # The failed write, plus at most the 3 other calls already in flight.
    assert 3 <= calls <= 6
    assert any("total=2" in note for note in excinfo.value.__notes__)


async def test_an_unexpected_runner_error_stops_the_run_with_its_summary(monkeypatch) -> None:
    real_evaluate = runner._evaluate
    evaluated = 0

    async def breaks_on_the_third(*args):
        nonlocal evaluated
        evaluated += 1
        if evaluated == 3:
            raise RuntimeError("runner bug")
        return await real_evaluate(*args)

    monkeypatch.setattr(runner, "_evaluate", breaks_on_the_third)
    before = asyncio.all_tasks()
    sink = MemorySink()
    with pytest.raises(RuntimeError, match="runner bug") as excinfo:
        await run(samples=5, max_concurrency=1, sink=sink)

    assert asyncio.all_tasks() == before
    assert len(sink.evaluations) == 2
    assert any("total=2" in note for note in excinfo.value.__notes__)


async def test_error_messages_are_stored_safely(tmp_path: Path) -> None:
    # An undecodable byte (a lone surrogate) and a huge body would both break the write.
    def score(input, output, context):
        raise ConnectionError("bad \udcff body " + "x" * 5000)

    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path) as sink:
        summary, _ = await run(score, samples=1, sink=sink, max_consecutive_errors=None)

    assert summary.errors == 6
    for e in read_evaluations(path):
        assert e.error.startswith("bad \\udcff body xxx")
        assert e.error.endswith("... (truncated)")
        assert len(e.error) <= runner.MAX_ERROR_CHARS + len("... (truncated)")


async def test_a_sink_that_awaits_never_sees_overlapping_writes_or_writes_after_failing() -> None:
    class SlowDatabase(MemorySink):
        """Awaits inside write, like a database insert, and fails on the third."""

        writing = peak = writes_after_failure = 0
        failed = False

        async def write(self, evaluation: Evaluation) -> None:
            if self.failed:
                self.writes_after_failure += 1
            self.writing += 1
            self.peak = max(self.peak, self.writing)
            await asyncio.sleep(0.01)
            self.writing -= 1
            if len(self.evaluations) == 2 and not self.failed:
                self.failed = True
                raise ConnectionError("connection lost")
            await super().write(evaluation)

    sink = SlowDatabase()
    with pytest.raises(ConnectionError, match="connection lost"):
        await run(samples=5, max_concurrency=4, sink=sink)

    assert sink.peak == 1
    assert sink.writes_after_failure == 0
    assert len(sink.evaluations) == 2


async def test_failed_calls_are_logged_with_the_stored_message(caplog) -> None:
    def score(input, output, context):
        raise ConnectionError("x" * 5000)

    with caplog.at_level(logging.WARNING, logger="mtlj.engine.runner"):
        await run(score, samples=1, max_consecutive_errors=None)

    assert "... (truncated)" in caplog.text
    assert "x" * (runner.MAX_ERROR_CHARS + 1) not in caplog.text


async def test_a_judge_failing_every_call_stops_the_run() -> None:
    calls = 0
    lock = threading.Lock()

    def score(input, output, context):
        nonlocal calls
        with lock:
            calls += 1
        always_fails(input, output, context)

    sink = MemorySink()
    with pytest.raises(
        RunAbortedError, match=r"5 judge calls in a row failed.*invalid API key"
    ) as excinfo:
        await run(score, samples=5, max_concurrency=4, max_consecutive_errors=5, sink=sink)

    # Stopped after 5 failures plus at most the 3 other calls already in flight, not all 30,
    # and every failure was still written.
    assert 5 <= calls <= 8
    assert len(sink.evaluations) == calls
    assert {e.status for e in sink.evaluations} == {ERROR}
    summary = excinfo.value.summary
    assert (summary.total, summary.ok, summary.errors) == (calls, 0, calls)
    assert summary.errors_by_type == {"PermissionError": calls}


async def test_a_success_resets_the_consecutive_error_count() -> None:
    # One worker: every sample runs 3 originals (fine) then 3 mutants (failing), so failures
    # never run more than 3 in a row.
    mutated = {m.mutated_text for m in MUTANTS}

    def score(input, output, context):
        if output in mutated:
            always_fails(input, output, context)
        return 0.5

    summary, _ = await run(score, samples=4, max_concurrency=1, max_consecutive_errors=4)

    assert (summary.ok, summary.errors) == (12, 12)


async def test_consecutive_errors_can_be_allowed_without_limit() -> None:
    summary, _ = await run(always_fails, samples=5, max_consecutive_errors=None)

    assert (summary.ok, summary.errors) == (0, 30)
    assert summary.errors_by_type == {"PermissionError": 30}


async def test_a_run_whose_last_calls_fail_still_completes() -> None:
    # One worker, so the 3 mutants of the last sample are the last 3 calls, and they fail.
    last_sample = {m.mutated_text for m in MUTANTS}
    calls = Counter()

    def score(input, output, context):
        calls[output] += 1
        if output in last_sample and calls[output] == 2:
            always_fails(input, output, context)
        return 0.5

    summary, sink = await run(score, samples=2, max_concurrency=1, max_consecutive_errors=3)

    assert (summary.total, summary.errors) == (12, 3)
    assert len(sink.evaluations) == 12


def test_run_aborted_error_survives_pickling_with_its_summary() -> None:
    summary = RunSummary("stub", "stub-model", "1.0", 5, total=7, errors=7)
    copy = pickle.loads(pickle.dumps(RunAbortedError("7 judge calls in a row failed", summary)))

    assert str(copy) == "7 judge calls in a row failed"
    assert copy.summary == summary


async def test_cancelling_the_run_stops_every_worker() -> None:
    started = threading.Event()

    def score(input, output, context):
        started.set()
        time.sleep(0.01)
        return 0.5

    before = asyncio.all_tasks()
    sink = MemorySink()
    task = asyncio.create_task(
        run_judge(judge(score), RECORDS, MUTANTS, sink, samples=50, max_concurrency=4)
    )
    async with asyncio.timeout(5):
        while not sink.evaluations:
            assert not task.done(), "the run ended before it could be cancelled"
            await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert asyncio.all_tasks() == before
    assert len(sink.evaluations) < 300


def test_run_judge_sync_runs_without_an_event_loop() -> None:
    sink = MemorySink()
    summary = run_judge_sync(judge(), RECORDS, MUTANTS, sink, samples=2)

    assert summary.ok == len(sink.evaluations) == 12


# --------------------------------------------------
# JSON Lines sink
# --------------------------------------------------


async def test_jsonl_sink_writes_one_readable_line_per_evaluation(tmp_path: Path) -> None:
    path = tmp_path / "run" / "evaluations.jsonl"
    with JsonlSink(path) as sink:
        _, _ = await run(samples=2, sink=sink)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    assert all(json.loads(line)["status"] == OK for line in lines)
    assert b"\r\n" not in path.read_bytes()
    assert {(e.target_id, e.sample_index) for e in read_evaluations(path)} == {
        (t.target_id, t.sample_index) for t in build_tasks(RECORDS, MUTANTS, 2)
    }


async def test_jsonl_sink_keeps_what_was_written_when_the_run_dies(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"

    class DiesAfterFour(JsonlSink):
        async def write(self, evaluation: Evaluation) -> None:
            await super().write(evaluation)
            if len(read_evaluations(self.path)) == 4:
                raise RuntimeError("killed")

    with DiesAfterFour(path) as sink, pytest.raises(RuntimeError, match="killed"):
        await run(samples=5, max_concurrency=1, sink=sink)

    assert len(read_evaluations(path)) == 4


def test_jsonl_sink_never_overwrites_an_earlier_run(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    path.write_text("earlier run\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        JsonlSink(path)
    assert path.read_text(encoding="utf-8") == "earlier run\n"


def test_two_runs_cant_share_a_file(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path), pytest.raises(FileExistsError):
        JsonlSink(path)


async def test_a_run_rejected_before_any_call_leaves_no_file(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path) as sink, pytest.raises(ValueError, match="is missing"):
        await run_judge(judge(), RECORDS[1:], MUTANTS, sink)

    assert not path.exists()
    JsonlSink(path).close()  # the same path can be used for the retry


async def test_a_partly_written_last_line_is_skipped(tmp_path: Path, caplog) -> None:
    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path) as sink:
        await run(samples=1, sink=sink)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write('{"kind": "mutant", "target_')  # the process died mid-write

    with caplog.at_level(logging.WARNING, logger="mtlj.engine.runner"):
        evaluations = read_evaluations(path)

    assert len(evaluations) == 6
    assert "partly written" in caplog.text


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"kind": "original"}',  # missing fields
        '{"kind": "original", "target_id": "t_0", "record_id": "t_0", "sample_index": 0, '
        '"judge_name": "j", "model": "m", "version": "1", "status": "ok", "latency_ms": 1, '
        '"score": "high"}',  # wrong type
    ],
)
def test_a_bad_line_mid_file_is_still_an_error(tmp_path: Path, line: str) -> None:
    path = tmp_path / "evaluations.jsonl"
    path.write_text(line + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(ValidationError):
        read_evaluations(path)


async def test_a_row_that_cant_be_serialized_leaves_no_file(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    unwritable = Evaluation.model_construct(
        **build_evaluation_fields(), error="bad \udcff body", status=ERROR
    )

    with JsonlSink(path) as sink, pytest.raises(PydanticSerializationError):
        await sink.write(unwritable)

    assert not path.exists()


def build_evaluation_fields() -> dict:
    return {
        "kind": ORIGINAL,
        "target_id": "t_0",
        "record_id": "t_0",
        "sample_index": 0,
        "judge_name": "stub",
        "model": "stub-model",
        "version": "1.0",
        "latency_ms": 1.0,
    }


async def test_a_first_write_that_fails_part_way_keeps_the_file(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"

    class BrokenFile:
        """Takes some bytes, then fails, like a disk that fills up mid-write."""

        def __init__(self, real) -> None:
            self.real = real
            self.closed = False

        def write(self, text: str) -> None:
            self.real.write(text[:10])
            self.real.flush()
            raise OSError("disk full")

        def close(self) -> None:
            self.closed = True
            self.real.close()

    sink = JsonlSink(path)
    sink._file = BrokenFile(sink._file)
    with sink, pytest.raises(OSError, match="disk full"):
        await run(samples=1, sink=sink)

    assert path.exists()
    assert len(path.read_text(encoding="utf-8")) == 10


# --------------------------------------------------
# End to end: corpus -> mutants -> stub judge -> file -> detection rate
# --------------------------------------------------


async def test_end_to_end_with_the_stub_adapter(tmp_path: Path) -> None:
    records = [
        DatasetRecord.model_validate_json(line)
        for line in EXAMPLE_RECORDS.read_text(encoding="utf-8").splitlines()
    ]
    generation = generate_mutants(records, default_battery(), base_seed=1)
    swaps = [m for m in generation.mutants if m.operator_name == "entity_swap"]
    assert swaps, "the example records should give at least one entity swap"

    # A deterministic stub that scores an answer high when it still contains the original
    # answer word for word. Each text's k-th call gets the k-th offset, so the scores have
    # some noise but are the same whatever order the calls finish in.
    answers = [r.answer for r in records]
    # The stub tells calls apart by text, so every original and mutant must have its own.
    texts = [*answers, *(m.mutated_text for m in generation.mutants)]
    assert len(set(texts)) == len(texts)
    offsets = [0.0, 0.02, -0.02, 0.01, -0.01]
    calls: defaultdict[str, int] = defaultdict(int)
    lock = threading.Lock()

    def score(input, output, context):
        with lock:
            k = calls[output]
            calls[output] += 1
        base = 0.9 if any(answer in output for answer in answers) else 0.3
        return base + offsets[k % len(offsets)]

    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path) as sink:
        summary = await run_judge(
            judge(score), generation.originals, generation.mutants, sink, samples=5
        )

    texts = len(generation.originals) + len(generation.mutants)
    assert summary.ok == summary.total == 5 * texts

    evaluations = read_evaluations(path)
    by_target: defaultdict[str, list[float]] = defaultdict(list)
    for e in evaluations:
        by_target[e.target_id].append(e.score)
    originals = {r.record_id: by_target[r.record_id] for r in generation.originals}
    result = detection_rate(
        originals, [MutantScores(m.record_id, by_target[m.mutant_id]) for m in swaps]
    )

    assert result.n_samples == 5
    assert result.testable == len(swaps)
    assert result.rate == 1.0


# --------------------------------------------------
# Limits: concurrency, rate limit and backpressure (MTLJ-96)
# --------------------------------------------------


class Limited(CallableJudge):
    """A judge that says how hard it may be driven, as an adapter would."""

    default_max_concurrency = 2
    default_rate_limit = 90.0


def limited(score_fn=lambda i, o, c: 0.5) -> Limited:
    return Limited(score_fn, name="stub", model="stub-model", version="1.0")


class Cacheable(Judge):
    """A judge the response cache accepts: its settings are JSON."""

    score_range = (0.0, 1.0)

    def __init__(self, score_fn=lambda i, o, c: 0.5) -> None:
        self.name, self.model, self.version = "stub", "stub-model", "1.0"
        self.prompt = "Rate the answer."
        self._score_fn = score_fn

    def score_raw(self, input, output, context):
        return self._score_fn(input, output, context)


class Recorder(RateLimiter):
    """A rate limiter that never really waits and counts the turns it gives."""

    made: ClassVar[list["Recorder"]] = []

    def __init__(self, calls_per_minute: float) -> None:
        clock = [0.0]

        async def sleep(seconds: float) -> None:
            clock[0] += seconds

        super().__init__(calls_per_minute, clock=lambda: clock[0], sleep=sleep)
        self.turns = 0
        Recorder.made.append(self)

    async def acquire(self, stop: asyncio.Event | None = None) -> bool:
        granted = await super().acquire(stop)
        self.turns += granted
        return granted


@pytest.fixture
def recorder(monkeypatch) -> type[Recorder]:
    Recorder.made = []
    monkeypatch.setattr(runner, "RateLimiter", Recorder)
    return Recorder


@pytest.mark.parametrize(
    ("judge_", "given", "expected"),
    [
        (judge(), (None, None), RunLimits(DEFAULT_MAX_CONCURRENCY, None)),
        (limited(), (None, None), RunLimits(2, 90.0)),
        (limited(), (8, 600), RunLimits(8, 600.0)),
        (limited(), (None, NO_RATE_LIMIT), RunLimits(2, None)),
        (limited(), (None, 0.0), RunLimits(2, None)),
        (judge(), (3, 12.5), RunLimits(3, 12.5)),
    ],
    ids=["runner-defaults", "judge-defaults", "given", "zero-is-none", "zero-float", "no-judge"],
)
def test_limits_given_win_then_the_judges_then_the_runners(judge_, given, expected) -> None:
    assert resolve_limits(judge_, *given) == expected


def test_resolved_limits_given_back_resolve_the_same() -> None:
    for given in [(None, None), (5, None), (None, NO_RATE_LIMIT), (1, 45.0)]:
        limits = resolve_limits(limited(), *given)
        again = (limits.max_concurrency, limits.rate_limit or NO_RATE_LIMIT)
        assert resolve_limits(limited(), *again) == limits


@pytest.mark.parametrize(
    ("given", "error", "message"),
    [
        ((0, None), ValueError, r"^max_concurrency must be at least 1, got 0"),
        ((True, None), TypeError, r"^max_concurrency must be an int"),
        ((None, -1), ValueError, r"^rate_limit must be a finite number"),
        ((None, math.inf), ValueError, r"^rate_limit must be a finite number"),
        ((None, True), TypeError, r"^rate_limit must be a number"),
    ],
)
def test_bad_limits_are_named(given, error, message) -> None:
    with pytest.raises(error, match=message):
        resolve_limits(limited(), *given)


@pytest.mark.parametrize(
    ("attr", "value", "error"),
    [
        ("default_max_concurrency", 0, ValueError),
        ("default_max_concurrency", 2.5, TypeError),
        ("default_rate_limit", 0, ValueError),
        ("default_rate_limit", "fast", TypeError),
    ],
)
async def test_a_judge_with_a_bad_default_limit_is_rejected_before_any_call(
    attr, value, error
) -> None:
    calls = []
    bad = type("Bad", (Limited,), {attr: value})(
        lambda i, o, c: calls.append(1) or 0.5, name="stub", model="m", version="1"
    )

    with pytest.raises(error, match=rf"^Bad\.{attr} must be"):
        await run_judge(bad, RECORDS, MUTANTS, MemorySink(), samples=1)
    assert calls == []


def test_a_judges_limits_dont_change_its_cache_keys(monkeypatch) -> None:
    # They don't change scores: giving an adapter limits must keep its cached and recorded
    # scores (the DeepEval fixture replay tests check the same for the real adapter).
    context = judge_context(RECORDS[0].context)
    before = CacheKeys(Cacheable())("q", "a", context, 0)

    monkeypatch.setattr(Cacheable, "default_max_concurrency", 1)
    monkeypatch.setattr(Cacheable, "default_rate_limit", 30.0)

    assert CacheKeys(Cacheable())("q", "a", context, 0) == before
    assert set(Cacheable().cache_settings()) == {"prompt"}

    # Not even when an instance sets its own, from a constructor argument say.
    own = Cacheable()
    own.default_rate_limit = 45.0
    own.default_max_concurrency = 2
    assert CacheKeys(own)("q", "a", context, 0) == before
    assert set(own.cache_settings()) == {"prompt"}


async def test_the_judges_default_limits_are_used_and_reported(recorder) -> None:
    summary = await run_judge(limited(), RECORDS, MUTANTS, MemorySink(), samples=2)

    assert (summary.max_concurrency, summary.rate_limit) == (2, 90.0)
    [made] = recorder.made
    assert made.calls_per_minute == 90.0
    assert made.turns == summary.total == 12
    # 12 calls at 90 a minute: all but the first wait, on the fake clock that's 11 turns.
    assert summary.rate_limited == 11
    assert summary.rate_limit_wait_s > 0


async def test_no_rate_limit_makes_no_limiter(recorder) -> None:
    summary = await run_judge(
        limited(), RECORDS, MUTANTS, MemorySink(), samples=1, rate_limit=NO_RATE_LIMIT
    )

    assert recorder.made == []
    assert (summary.rate_limit, summary.rate_limited, summary.rate_limit_wait_s) == (None, 0, 0.0)


async def test_every_retry_waits_for_its_turn(recorder) -> None:
    # Each text's first call times out and its retry works: twice as many calls as scorings.
    seen: set[str] = set()
    lock = threading.Lock()

    def flaky(input, output, context):
        with lock:
            first = output not in seen
            seen.add(output)
        if first:
            raise TimeoutError("read timed out")
        return 0.5

    retry = RetryPolicy(max_attempts=2, base_delay_s=0)
    summary, sink = await run(flaky, samples=1, retry=retry, rate_limit=600)

    [made] = recorder.made
    assert summary.retries == 6
    assert made.turns == sum(e.attempts for e in sink.evaluations) == 12


async def test_scorings_the_cache_answers_dont_wait_for_a_turn(recorder, tmp_path: Path) -> None:
    path = tmp_path / "responses.sqlite3"
    with ResponseCache(path) as cache:
        await run_judge(
            Cacheable(), RECORDS, MUTANTS, MemorySink(), cache=cache, samples=2, rate_limit=60
        )
    with ResponseCache(path) as cache:
        summary = await run_judge(
            Cacheable(), RECORDS, MUTANTS, MemorySink(), cache=cache, samples=2, rate_limit=60
        )

    first, second = recorder.made
    assert first.turns == summary.total == 12
    assert (second.turns, summary.cache_hits, summary.rate_limited) == (0, 12, 0)


async def test_a_run_that_stops_makes_no_call_still_waiting_for_its_turn() -> None:
    # One call a minute on the real clock: the first fails, which stops the run, while the
    # second worker waits ~60 s for its turn. It must give up at once and write nothing.
    calls = []

    def fails(input, output, context):
        calls.append(output)
        raise PermissionError("invalid API key")

    sink = MemorySink()
    started = time.perf_counter()
    with pytest.raises(RunAbortedError) as raised:
        await run_judge(
            judge(fails),
            RECORDS,
            MUTANTS,
            sink,
            samples=1,
            max_concurrency=2,
            rate_limit=1,
            max_consecutive_errors=1,
            retry=NO_RETRY,
        )

    assert time.perf_counter() - started < 5
    assert len(calls) == len(sink.evaluations) == raised.value.summary.total == 1


class StopsAfter:
    """A stand-in limiter that gives ``turns`` turns, then acts as if the run stopped."""

    def __init__(self, turns: int) -> None:
        self.turns = turns

    async def acquire(self, stop=None) -> bool:
        self.turns -= 1
        return self.turns >= 0


async def test_a_retry_that_never_gets_its_turn_records_the_failure_before_it() -> None:
    task = next(iter(build_tasks(RECORDS, [], 1)))

    def times_out(input, output, context):
        raise TimeoutError("read timed out")

    with ThreadPoolExecutor(max_workers=1) as executor:
        evaluation = await runner._evaluate(
            judge(times_out),
            task,
            executor,
            RetryPolicy(max_attempts=5, base_delay_s=0),
            asyncio.Event(),
            StopsAfter(1),
        )

    assert evaluation is not None
    assert (evaluation.status, evaluation.error_type, evaluation.error_kind) == (
        ERROR,
        "TimeoutError",
        "transient",
    )
    # One call made; the retry it was due never ran, so it isn't counted or named.
    assert (evaluation.attempts, evaluation.retry_errors) == (1, ())


async def test_a_scoring_that_never_got_a_turn_has_nothing_to_record() -> None:
    task = next(iter(build_tasks(RECORDS, [], 1)))
    calls = []

    with ThreadPoolExecutor(max_workers=1) as executor:
        evaluation = await runner._evaluate(
            judge(lambda i, o, c: calls.append(1) or 0.5),
            task,
            executor,
            NO_RETRY,
            asyncio.Event(),
            StopsAfter(0),
        )

    assert evaluation is None
    assert calls == []


# The slow stub: a judge whose calls take real time, as a hosted one's do.


class SlowStub:
    """Takes ``seconds`` per call, and records when each call starts and how many overlap."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.starts: list[float] = []
        self.in_flight = self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, input, output, context) -> float:
        with self.lock:
            self.starts.append(time.perf_counter())
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(self.seconds)
        with self.lock:
            self.in_flight -= 1
        return 0.5


async def test_a_slow_judge_is_driven_at_the_rate_limit_and_no_harder() -> None:
    # 12 calls of 50 ms, 3 at a time, at most 600 a minute: one start every 100 ms. Without
    # the limit they'd take 0.2 s; with it, at least 1.1 s.
    stub = SlowStub(0.05)

    summary, sink = await run(stub, samples=2, max_concurrency=3, rate_limit=600)

    assert summary.ok == len(sink.evaluations) == 12
    assert stub.peak <= 3
    starts = sorted(stub.starts)
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    # Turns are 100 ms apart; a thread can start a little late, so allow for that. Without the
    # limit, 3 calls start together, 0 ms apart.
    assert min(gaps) >= 0.05
    assert starts[-1] - starts[0] >= 1.1 - 0.02
    assert summary.rate_limited >= 10


async def test_a_slow_judge_without_a_rate_limit_runs_at_full_concurrency() -> None:
    stub = SlowStub(0.05)

    summary, _ = await run(stub, samples=2, max_concurrency=3, rate_limit=NO_RATE_LIMIT)

    assert summary.ok == 12
    assert stub.peak == 3
    assert summary.rate_limited == 0


async def test_tasks_are_only_made_as_workers_free_up(monkeypatch) -> None:
    # Backpressure: however big the run, at most max_concurrency tasks are made and unfinished.
    made = finished = worst = 0
    lock = threading.Lock()
    real_build = runner.build_tasks

    class Counted:
        def __init__(self, plan) -> None:
            self.plan = plan

        def __len__(self) -> int:
            return len(self.plan)

        def __iter__(self):
            nonlocal made
            for task in self.plan:
                made += 1
                yield task

    monkeypatch.setattr(runner, "build_tasks", lambda *a: Counted(real_build(*a)))

    def score(input, output, context):
        nonlocal finished, worst
        time.sleep(0.005)
        with lock:
            worst = max(worst, made - finished)
            finished += 1
        return 0.5

    many = [_record(n) for n in range(40)]
    summary = await run_judge(judge(score), many, [], MemorySink(), samples=5, max_concurrency=4)

    assert summary.ok == 200
    assert worst <= 4
