"""Tests for retrying transient judge failures (MTLJ-94)."""

import hashlib
import inspect
import logging
import math
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.retry import (
    DEFAULT_RETRY,
    NO_RETRY,
    PERMANENT,
    TRANSIENT,
    RetryPolicy,
    is_transient,
    retry_after,
    status_code,
)
from mtlj.engine.runner import (
    ERROR,
    OK,
    JsonlSink,
    MemorySink,
    RunAbortedError,
    read_evaluations,
    run_judge,
    run_judge_sync,
)
from mtlj.judges.callable import CallableJudge
from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)

openai = pytest.importorskip("openai")

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


class ServiceError(Exception):
    """An SDK-style error carrying an HTTP status and response headers."""

    def __init__(self, status: int, headers: dict | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


def openai_error(cls, status: int, *, code: str | None = None, headers: dict | None = None):
    response = httpx.Response(status, request=REQUEST, headers=headers)
    body = {"code": code, "message": "error", "type": "error"} if code else None
    return cls("error", response=response, body=body)


def caused_by(exc: Exception, cause: BaseException) -> Exception:
    exc.__cause__ = cause
    return exc


class RetryError(Exception):
    """Stands in for tenacity's RetryError, which DeepEval raises from the last error."""


# --------------------------------------------------
# Which failures are transient
# --------------------------------------------------


TRANSIENT_CASES = [
    TimeoutError("read timed out"),
    ConnectionError("connection refused"),
    ConnectionResetError("connection reset by peer"),
    httpx.ReadTimeout("timed out", request=REQUEST),
    httpx.ConnectError("connection refused", request=REQUEST),
    httpx.RemoteProtocolError("server disconnected", request=REQUEST),
    openai.APITimeoutError(request=REQUEST),
    openai.APIConnectionError(request=REQUEST),
    openai_error(openai.RateLimitError, 429, code="rate_limit_exceeded"),
    openai_error(openai.RateLimitError, 429),
    openai_error(openai.InternalServerError, 500),
    openai_error(openai.InternalServerError, 503),
    openai_error(openai.ConflictError, 409),
    httpx.HTTPStatusError("bad gateway", request=REQUEST, response=httpx.Response(502)),
    ServiceError(408),
    ServiceError(504),
    caused_by(RetryError("gave up after 2 attempts"), openai.APITimeoutError(request=REQUEST)),
    caused_by(RuntimeError("wrapped"), caused_by(RetryError(), TimeoutError())),
]

PERMANENT_CASES = [
    ValueError("score out of range"),
    TypeError("expected a real number"),
    RuntimeError("adapter bug"),
    KeyError("passages"),
    PermissionError("invalid API key"),  # an OSError, but not a connection error
    FileNotFoundError("model weights"),
    openai_error(openai.AuthenticationError, 401),
    openai_error(openai.PermissionDeniedError, 403),
    openai_error(openai.NotFoundError, 404),
    openai_error(openai.BadRequestError, 400),
    openai_error(openai.UnprocessableEntityError, 422),
    openai_error(openai.RateLimitError, 429, code="insufficient_quota"),
    ServiceError(400),
    httpx.UnsupportedProtocol("no scheme", request=REQUEST),
    caused_by(RetryError(), openai_error(openai.AuthenticationError, 401)),
    caused_by(ValueError("no verdicts"), KeyError("verdicts")),
]


@pytest.mark.parametrize("exc", TRANSIENT_CASES, ids=lambda e: type(e).__name__)
def test_transient_failures(exc: BaseException) -> None:
    assert is_transient(exc)


@pytest.mark.parametrize("exc", PERMANENT_CASES, ids=lambda e: type(e).__name__)
def test_permanent_failures(exc: BaseException) -> None:
    assert not is_transient(exc)


def test_out_of_quota_is_read_from_the_message_when_there_is_no_code() -> None:
    class QuotaError(ServiceError):
        def __str__(self) -> str:
            return "You exceeded your current quota, please check your plan and billing details."

    assert not is_transient(QuotaError(429))
    assert is_transient(ServiceError(429))


def test_the_first_exception_in_the_chain_that_says_decides() -> None:
    # The wrapper has a status, so its cause isn't consulted.
    assert not is_transient(caused_by(ServiceError(400), TimeoutError()))
    assert is_transient(caused_by(ServiceError(503), ValueError()))


def test_a_chain_that_loops_ends() -> None:
    a, b = RetryError("a"), RetryError("b")
    a.__cause__, b.__cause__ = b, a

    assert not is_transient(a)


def test_status_code_is_read_from_the_exception_or_its_response() -> None:
    assert status_code(ServiceError(503)) == 503
    assert (
        status_code(httpx.HTTPStatusError("x", request=REQUEST, response=httpx.Response(418)))
        == 418
    )
    assert status_code(ValueError()) is None
    flagged = ValueError()
    flagged.status_code = True  # not a status
    assert status_code(flagged) is None


# --------------------------------------------------
# How long to wait
# --------------------------------------------------


def test_waits_grow_exponentially_up_to_the_cap() -> None:
    policy = RetryPolicy(jitter=0)

    assert [policy.delay(k) for k in range(1, 9)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_the_default_waits_at_most_31_seconds_over_5_retries() -> None:
    assert DEFAULT_RETRY.max_attempts == 6
    longest = [DEFAULT_RETRY.delay(k, rng=lambda: 0.0) for k in range(1, 6)]
    shortest = [DEFAULT_RETRY.delay(k, rng=lambda: 1.0) for k in range(1, 6)]

    assert sum(longest) == 31
    assert sum(shortest) == 15.5


def test_jitter_cuts_each_wait_by_up_to_its_share() -> None:
    policy = RetryPolicy(base_delay_s=4, jitter=0.25)
    waits = [policy.delay(1) for _ in range(200)]

    assert all(3 <= w <= 4 for w in waits)
    assert len(set(waits)) > 1


def test_a_huge_retry_number_waits_the_cap() -> None:
    assert RetryPolicy(jitter=0, multiplier=10).delay(10_000) == 60
    assert RetryPolicy(base_delay_s=0).delay(10_000) == 0


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "7"}, 7.0),
        ({"Retry-After": "7"}, 7.0),
        ({"retry-after": "120"}, 60.0),  # capped
        ({"retry-after": "0.2"}, 1.0),  # the backoff is longer
        ({"retry-after-ms": "2500"}, 2.5),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, 1.0),  # a date: ignored
        ({"retry-after": "-3"}, 1.0),
        ({"retry-after": "nan"}, 1.0),
        ({}, 1.0),
    ],
)
def test_a_servers_retry_after_lengthens_the_wait(headers: dict, expected: float) -> None:
    policy = RetryPolicy(jitter=0)

    assert policy.delay(1, ServiceError(429, headers)) == expected


def test_retry_after_from_a_real_openai_error_and_through_a_chain() -> None:
    rate_limited = openai_error(openai.RateLimitError, 429, headers={"retry-after": "9"})

    assert retry_after(rate_limited) == 9.0
    assert retry_after(caused_by(RetryError(), rate_limited)) == 9.0
    assert retry_after(ValueError()) is None
    attr = TimeoutError()
    attr.retry_after = 3
    assert RetryPolicy(jitter=0).delay(1, attr) == 3.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "max_attempts must be at least 1"),
        ({"base_delay_s": -1}, "base_delay_s"),
        ({"max_delay_s": math.inf}, "max_delay_s"),
        ({"multiplier": 0.5}, "multiplier"),
        ({"multiplier": math.nan}, "multiplier"),
        ({"jitter": 1.5}, "jitter"),
    ],
)
def test_rejects_a_bad_policy(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RetryPolicy(**kwargs)


@pytest.mark.parametrize("value", [2.0, True, "3"])
def test_max_attempts_must_be_an_int(value) -> None:
    with pytest.raises(TypeError, match="max_attempts"):
        RetryPolicy(max_attempts=value)


def test_rejects_a_retry_number_below_one() -> None:
    with pytest.raises(ValueError, match="retry must be at least 1"):
        DEFAULT_RETRY.delay(0)


# --------------------------------------------------
# The runner, against an intermittently failing stub
# --------------------------------------------------


def _record(n: int) -> DatasetRecord:
    return DatasetRecord(
        record_id=f"t_{n}",
        source_dataset="test",
        question=f"Where was conference {n} held?",
        context=[{"passage_id": "p1", "text": "The annual conference took place in Paris."}],
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


RECORDS = [_record(n) for n in range(4)]
MUTANTS = [Mutant.generate(r, Shout(), 1.0, seed=7) for r in RECORDS]
FAST = RetryPolicy(max_attempts=10, base_delay_s=0)


def score_for(text: str) -> float:
    """A score that depends only on the text, so a flaky run can be checked against a clean one."""
    return int(hashlib.sha256(text.encode()).hexdigest(), 16) % 1000 / 1000


def judge(score_fn) -> CallableJudge:
    return CallableJudge(score_fn, name="stub", model="stub-model", version="1.0")


def transient_error(n: int) -> Exception:
    """The transient errors real backends raise, in turn."""
    return [
        TimeoutError("read timed out"),
        ConnectionResetError("connection reset by peer"),
        ServiceError(503),
        ServiceError(429, {"retry-after": "0"}),
        openai.APITimeoutError(request=REQUEST),
        caused_by(RetryError(), openai_error(openai.RateLimitError, 429)),
    ][n % 6]


class Flaky:
    """Fails a share of calls with transient errors, and otherwise scores the text.

    Whether a text's k-th call fails depends only on the text and k, so the same calls fail
    whatever order the workers run them in.
    """

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.calls: Counter[str] = Counter()
        self.failures: Counter[str] = Counter()
        self.lock = threading.Lock()

    def __call__(self, input, output, context) -> float:
        with self.lock:
            k = self.calls[output]
            self.calls[output] += 1
        digest = hashlib.sha256(f"{output}|{k}".encode()).digest()
        if digest[0] < 256 * self.rate:
            error = transient_error(digest[1])
            with self.lock:
                self.failures[type(error).__name__] += 1
            raise error
        return score_for(output)


async def run(score_fn, *, samples=3, retry=FAST, sink=None, **kwargs):
    sink = sink if sink is not None else MemorySink()
    summary = await run_judge(
        judge(score_fn), RECORDS, MUTANTS, sink, samples=samples, retry=retry, **kwargs
    )
    return summary, sink


async def test_an_intermittently_failing_judge_scores_every_text() -> None:
    flaky = Flaky(rate=0.4)
    summary, sink = await run(flaky, samples=5, max_concurrency=4)
    _, clean = await run(lambda i, o, c: score_for(o), samples=5)

    texts = 5 * (len(RECORDS) + len(MUTANTS))
    assert summary.total == summary.ok == texts
    assert summary.errors == summary.gave_up == 0
    # The stub really did fail, and each failure was retried once.
    assert summary.retries == sum(flaky.failures.values()) > 10
    assert summary.retries_by_type == dict(flaky.failures)
    assert summary.retried == sum(e.attempts > 1 for e in sink.evaluations)
    assert sum(e.attempts for e in sink.evaluations) == sum(flaky.calls.values())
    # Retries don't change a score.
    scores = {(e.target_id, e.sample_index): e.score for e in sink.evaluations}
    assert scores == {(e.target_id, e.sample_index): e.score for e in clean.evaluations}
    for e in sink.evaluations:
        assert e.status == OK and e.error_kind is None
        assert e.attempts == len(e.retry_errors) + 1


async def test_a_transient_failure_that_outlasts_its_attempts_is_recorded() -> None:
    stuck = MUTANTS[1].mutated_text
    calls = Counter()

    def score(input, output, context):
        calls[output] += 1
        if output == stuck:
            raise TimeoutError("read timed out")
        return 0.5

    policy = RetryPolicy(max_attempts=3, base_delay_s=0)
    summary, sink = await run(score, samples=2, retry=policy)

    assert calls[stuck] == 3 * 2
    failed = [e for e in sink.evaluations if e.status == ERROR]
    assert {e.target_id for e in failed} == {MUTANTS[1].mutant_id}
    for e in failed:
        assert (e.error_type, e.error_kind, e.attempts) == ("TimeoutError", TRANSIENT, 3)
        assert e.retry_errors == ("TimeoutError", "TimeoutError")
        assert e.score is None
    assert (summary.errors, summary.gave_up, summary.retried, summary.retries) == (2, 2, 2, 4)
    assert summary.ok == summary.total - 2


async def test_a_permanent_failure_is_recorded_against_its_mutant_without_a_retry() -> None:
    bad = MUTANTS[2]
    calls = Counter()

    def score(input, output, context):
        calls[output] += 1
        if output == bad.mutated_text:
            raise PermissionError("invalid API key")
        if output == RECORDS[0].answer:
            return 7.0  # out of range: Judge.__call__ raises ValueError
        return 0.5

    summary, sink = await run(score, samples=3)

    assert calls[bad.mutated_text] == calls[RECORDS[0].answer] == 3
    failed = [e for e in sink.evaluations if e.status == ERROR]
    assert {e.target_id for e in failed} == {bad.mutant_id, "t_0"}
    assert {e.error_type for e in failed} == {"PermissionError", "ValueError"}
    for e in failed:
        assert (e.error_kind, e.attempts, e.retry_errors) == (PERMANENT, 1, ())
    # The run carried on.
    assert summary.ok == summary.total - 6
    assert (summary.errors, summary.gave_up, summary.retries) == (6, 0, 0)


async def test_retrying_stops_at_a_permanent_failure() -> None:
    calls = Counter()

    def score(input, output, context):
        calls[output] += 1
        if output != RECORDS[3].answer:
            return 0.5
        if calls[output] == 1:
            raise ConnectionError("connection reset")
        raise ValueError("no verdicts")

    summary, sink = await run(score, samples=1)

    (failed,) = [e for e in sink.evaluations if e.status == ERROR]
    assert (failed.error_type, failed.error_kind, failed.attempts) == ("ValueError", PERMANENT, 2)
    assert failed.retry_errors == ("ConnectionError",)
    assert (summary.errors, summary.gave_up, summary.retries) == (1, 0, 1)


async def test_without_retries_a_transient_failure_is_recorded_at_once() -> None:
    def score(input, output, context):
        if output == RECORDS[0].answer:
            raise TimeoutError("read timed out")
        return 0.5

    summary, sink = await run(score, samples=1, retry=NO_RETRY)

    (failed,) = [e for e in sink.evaluations if e.status == ERROR]
    assert (failed.error_kind, failed.attempts) == (TRANSIENT, 1)
    assert (summary.errors, summary.gave_up, summary.retries) == (1, 1, 0)


async def test_the_backoff_waits_longer_before_each_retry() -> None:
    times: defaultdict[str, list[float]] = defaultdict(list)

    def score(input, output, context):
        times[output].append(time.monotonic())
        if output == RECORDS[0].answer and len(times[output]) <= 2:
            raise TimeoutError("read timed out")
        return 0.5

    policy = RetryPolicy(max_attempts=3, base_delay_s=0.1, jitter=0)
    summary, _ = await run(score, samples=1, retry=policy)

    first, second, third = times[RECORDS[0].answer]
    # 0.1 s, then 0.2 s, less the event loop's clock resolution (about 16 ms on Windows).
    assert second - first >= 0.08
    assert third - second >= 0.18
    assert summary.ok == summary.total


async def test_a_call_waiting_to_retry_doesnt_hold_up_the_others() -> None:
    times: defaultdict[str, list[float]] = defaultdict(list)
    slow = RECORDS[0].answer

    def score(input, output, context):
        times[output].append(time.monotonic())
        if output == slow and len(times[output]) == 1:
            raise TimeoutError("read timed out")
        return 0.5

    policy = RetryPolicy(max_attempts=2, base_delay_s=0.3, jitter=0)
    summary, _ = await run(score, samples=1, retry=policy, max_concurrency=2)

    retried_at = times[slow][1]
    others = [t for text, ts in times.items() if text != slow for t in ts]
    assert len(others) == summary.total - 1
    assert max(others) < retried_at


async def test_a_run_that_stops_doesnt_wait_out_a_retry() -> None:
    waiting = RECORDS[0].answer

    def score(input, output, context):
        if output == waiting:
            raise TimeoutError("read timed out")
        raise PermissionError("invalid API key")

    policy = RetryPolicy(max_attempts=5, base_delay_s=30)
    sink = MemorySink()
    started = time.monotonic()
    with pytest.raises(RunAbortedError) as excinfo:
        await run(
            score, samples=1, retry=policy, max_concurrency=2, max_consecutive_errors=3, sink=sink
        )

    assert time.monotonic() - started < 10
    (stopped,) = [e for e in sink.evaluations if e.target_id == "t_0"]
    assert (stopped.error_kind, stopped.attempts, stopped.retry_errors) == (TRANSIENT, 1, ())
    assert excinfo.value.summary.gave_up == 1


async def test_a_failing_sink_doesnt_wait_out_a_retry() -> None:
    class FullDisk(MemorySink):
        async def write(self, evaluation) -> None:
            raise OSError("disk full")

    def score(input, output, context):
        if output == RECORDS[0].answer:
            raise TimeoutError("read timed out")
        return 0.5

    policy = RetryPolicy(max_attempts=5, base_delay_s=30)
    started = time.monotonic()
    with pytest.raises(OSError, match="disk full"):
        await run(score, samples=1, retry=policy, max_concurrency=2, sink=FullDisk())

    assert time.monotonic() - started < 10


async def test_retries_are_logged(caplog) -> None:
    def score(input, output, context):
        if output == RECORDS[0].answer and not hasattr(score, "failed"):
            score.failed = True
            raise ConnectionError("connection reset")
        return 0.5

    with caplog.at_level(logging.INFO, logger="mtlj.engine.runner"):
        await run(score, samples=1)

    assert "attempt 1 of 10" in caplog.text
    assert "retrying in 0.0 s" in caplog.text


async def test_retries_are_stored_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path) as sink:
        _, _ = await run(Flaky(rate=0.4), samples=2, sink=sink)
    _, memory = await run(Flaky(rate=0.4), samples=2)

    def rows(evaluations):
        return {
            (e.target_id, e.sample_index): e.model_dump(exclude={"latency_ms"}) for e in evaluations
        }

    stored = rows(read_evaluations(path))
    assert stored == rows(memory.evaluations)
    assert any(row["retry_errors"] for row in stored.values())


def test_rows_written_before_retries_still_read(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    path.write_text(
        '{"kind": "original", "target_id": "t_0", "record_id": "t_0", "sample_index": 0, '
        '"judge_name": "j", "model": "m", "version": "1", "status": "ok", "latency_ms": 1, '
        '"score": 0.5, "raw_score": 0.5}\n',
        encoding="utf-8",
        newline="\n",
    )

    (row,) = read_evaluations(path)

    assert (row.attempts, row.retry_errors, row.error_kind) == (1, (), None)


def test_runs_retry_by_default() -> None:
    for fn in (run_judge, run_judge_sync):
        assert inspect.signature(fn).parameters["retry"].default is DEFAULT_RETRY


def test_run_judge_sync_passes_the_policy_on() -> None:
    flaky = Flaky(rate=0.4)
    summary = run_judge_sync(judge(flaky), RECORDS, MUTANTS, MemorySink(), samples=2, retry=FAST)

    assert summary.ok == summary.total
    assert summary.retries == sum(flaky.failures.values()) > 0
