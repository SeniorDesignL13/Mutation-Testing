"""Contract every judge adapter must pass (MTLJ-66).

The rest of mtlj relies on these guarantees, so a new adapter can't quietly break them:
scores are floats in the declared range, odd or empty context doesn't crash, the judge
reports its model and version, every call reaches the backend (no caching), verdict counts
follow the 2026-10-20 rule, backend errors surface, and concurrent calls are safe.

## Adding an adapter

Write a harness factory that wires the adapter's backend to ``Harness.respond`` (a fake,
no network), then add it to ADAPTERS. ``test_every_adapter_is_registered`` fails until you
do. Each check is a plain function, and the broken judges at the bottom prove each check
catches the bug it is meant to.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import math
import pkgutil
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

import pytest

import mtlj.judges
from mtlj.judges.base import Context, Judge, JudgeResult, RawScore, VerdictCounts
from mtlj.judges.callable import CallableJudge


class BackendError(RuntimeError):
    """Raised by the fake backend to simulate a failed judge call."""


# --------------------------------------------------
# Harness
# --------------------------------------------------


@dataclass
class Harness:
    """An adapter wired to a fake backend the checks control.

    Queue what the backend should return next (a number, a RawScore, or an exception to
    raise); with an empty queue it returns the midpoint of the judge's range. ``calls``
    records every time the backend was actually hit.
    """

    reports_verdicts: bool
    errors: tuple[type[Exception], ...]
    judge: Judge = field(init=False)
    queue: deque = field(default_factory=deque)
    calls: list = field(default_factory=list)

    def respond(self, *call: Any) -> Any:
        self.calls.append(call)
        if not self.queue:
            low, high = self.judge.score_range
            return (low + high) / 2
        item = self.queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


Make = Callable[[], Harness]


def make_callable() -> Harness:
    harness = Harness(reports_verdicts=True, errors=(BackendError,))
    harness.judge = CallableJudge(harness.respond)
    return harness


# One entry per concrete Judge subclass in mtlj.judges.
ADAPTERS: dict[type[Judge], Make] = {
    CallableJudge: make_callable,
}


# --------------------------------------------------
# Shared assertions
# --------------------------------------------------

PASSAGE = {"passage_id": "p1", "text": "Paris is the capital of France.", "title": None}
PASSAGES = [
    PASSAGE,
    {"passage_id": "p2", "text": "France is in Western Europe.", "title": "France"},
    {"passage_id": "p3", "text": "The Seine flows through Paris.", "title": None},
]


def assert_valid_result(result: Any, judge: Judge) -> None:
    """Everything any JudgeResult from any adapter must satisfy."""
    assert isinstance(result, JudgeResult)
    hash(result)

    assert type(result.score) is float
    assert math.isfinite(result.score)
    assert 0.0 <= result.score <= 1.0

    low, high = judge.score_range
    assert type(result.raw_score) is float
    assert low <= result.raw_score <= high

    for value in (result.judge_name, result.model, result.version):
        assert isinstance(value, str)
        assert value
    assert result.judge_name == judge.name
    assert result.model == judge.model
    assert result.version == judge.version
    assert result.score_range == judge.score_range

    if result.n_claims is not None:
        assert type(result.n_claims) is int
        assert result.n_claims >= 0
    if result.verdict_counts is not None:
        assert isinstance(result.verdict_counts, VerdictCounts)
        assert all(type(count) is int and count >= 0 for count in result.verdict_counts)
        assert result.n_claims is not None
        assert sum(result.verdict_counts) <= result.n_claims


def assert_raises(errors: tuple[type[BaseException], ...], fn: Callable[[], Any]) -> None:
    """Assert fn raises one of errors, failing with an AssertionError otherwise."""
    try:
        result = fn()
    except errors:
        return
    raise AssertionError(f"expected one of {[e.__name__ for e in errors]}, got {result!r}")


def score(harness: Harness, *, context: Context | None = None) -> JudgeResult:
    result = harness.judge("What is the capital of France?", "Paris.", context or {})
    assert_valid_result(result, harness.judge)
    return result


# --------------------------------------------------
# Checks: score is a float within the declared range
# --------------------------------------------------


def check_declares_valid_range(make: Make) -> None:
    judge = make().judge
    assert isinstance(judge.score_range, tuple)
    assert len(judge.score_range) == 2
    low, high = judge.score_range
    assert math.isfinite(low)
    assert math.isfinite(high)
    assert low < high
    assert isinstance(judge.higher_is_better, bool)


def check_scores_at_range_points(make: Make) -> None:
    harness = make()
    low, high = harness.judge.score_range
    harness.queue.extend([low, (low + high) / 2, high])

    scores = [score(harness).score for _ in range(3)]

    expected = [0.0, 0.5, 1.0] if harness.judge.higher_is_better else [1.0, 0.5, 0.0]
    assert scores == pytest.approx(expected)


def check_integer_score_becomes_float(make: Make) -> None:
    harness = make()
    low, high = harness.judge.score_range
    integer = math.ceil(low)
    if integer > high:
        return  # No int fits in this range, so an int can't be a valid score here.
    harness.queue.append(integer)

    result = score(harness)  # assert_valid_result checks raw_score and score are floats

    assert result.raw_score == integer


def check_rejects_out_of_range_scores(make: Make) -> None:
    low, high = make().judge.score_range
    span = high - low
    for bad in (low - span, high + span, math.nan, math.inf, -math.inf, 10**400):
        harness = make()
        harness.queue.append(bad)
        assert_raises((ValueError, *harness.errors), lambda h=harness: score(h))


def check_rejects_non_numeric_scores(make: Make) -> None:
    for bad in (None, "0.5", True, object()):
        harness = make()
        harness.queue.append(bad)
        assert_raises((TypeError, ValueError, *harness.errors), lambda h=harness: score(h))


# --------------------------------------------------
# Checks: empty and minimal context
# --------------------------------------------------

CONTEXTS = [
    {},
    {"passages": []},
    {"passages": [PASSAGE]},
    {"passages": PASSAGES},
    MappingProxyType({"passages": PASSAGES}),
]
TEXTS = [
    ("", ""),
    ("What is the capital of France?", "Paris."),
    ("   ", "\n"),
    ("Qu'est-ce que la capitale ? 🇫🇷", "Ligne 1\nLigne 2\tavec tab"),
]


def check_handles_empty_and_minimal_context(make: Make) -> None:
    harness = make()
    for context in CONTEXTS:
        for input, output in TEXTS:
            result = harness.judge(input, output, context)
            assert_valid_result(result, harness.judge)


def check_does_not_mutate_context(make: Make) -> None:
    harness = make()
    context = {"passages": copy.deepcopy(PASSAGES)}
    before = copy.deepcopy(context)

    score(harness, context=context)

    assert context == before


# --------------------------------------------------
# Checks: model and version reported
# --------------------------------------------------


def check_reports_stable_metadata(make: Make) -> None:
    harness = make()
    first, second = score(harness), score(harness)

    def metadata(r: JudgeResult) -> tuple:
        return (r.judge_name, r.model, r.version, r.score_range)

    assert metadata(first) == metadata(second)


# --------------------------------------------------
# Checks: repeated calls without caching
# --------------------------------------------------


def check_calls_backend_every_time(make: Make) -> None:
    harness = make()
    for _ in range(5):
        score(harness)

    assert len(harness.calls) == 5


def check_returns_fresh_scores(make: Make) -> None:
    harness = make()
    low, high = harness.judge.score_range
    harness.queue.extend([low, high, low])

    raws = [score(harness).raw_score for _ in range(3)]

    assert raws == [low, high, low]


def check_instances_do_not_share_state(make: Make) -> None:
    first, second = make(), make()
    low, high = first.judge.score_range
    first.queue.extend([low, low])
    second.queue.extend([high, high])

    raws = [score(h).raw_score for h in (first, second, first, second)]

    assert raws == [low, high, low, high]
    assert len(first.calls) == 2
    assert len(second.calls) == 2


def check_accepts_positional_and_keyword_calls(make: Make) -> None:
    harness = make()
    try:
        positional = harness.judge("input", "output", {})
        keyword = harness.judge(input="input", output="output", context={})
    except TypeError as exc:
        raise AssertionError(f"judge must accept positional and keyword calls: {exc}") from exc

    assert_valid_result(positional, harness.judge)
    assert_valid_result(keyword, harness.judge)


# --------------------------------------------------
# Checks: verdict counts (2026-10-20 decision)
# --------------------------------------------------


def check_verdict_counts(make: Make) -> None:
    harness = make()
    low, high = harness.judge.score_range

    if not harness.reports_verdicts:
        # Must not invent verdicts, e.g. by deriving them from the score.
        for raw in (low, (low + high) / 2, high):
            harness.queue.append(raw)
            result = score(harness)
            assert result.verdict_counts is None
        return

    cases = [
        (VerdictCounts(5, 2, 0), 7),  # Sums to n_claims
        (VerdictCounts(13, 1, 0), 15),  # Fewer verdicts than claims (DeepEval)
        (VerdictCounts(0, 0, 0), 0),  # No claims
    ]
    for counts, n_claims in cases:
        harness.queue.append(RawScore(high, verdict_counts=counts, n_claims=n_claims))
        result = score(harness)
        assert result.verdict_counts == counts
        assert result.n_claims == n_claims

    harness.queue.append(high)
    assert score(harness).verdict_counts is None


# --------------------------------------------------
# Checks: errors and concurrency (relied on by the async runner, MTLJ-95)
# --------------------------------------------------


def check_backend_errors_propagate(make: Make) -> None:
    harness = make()
    harness.queue.append(BackendError("backend is down"))

    assert_raises(harness.errors, lambda: score(harness))


def check_concurrent_calls(make: Make) -> None:
    harness = make()
    low, high = harness.judge.score_range
    # A different score per call, so threads swapping or overwriting results shows up
    # as a lost or duplicated score.
    expected = [low + (high - low) * i / 19 for i in range(20)]
    harness.queue.extend(expected)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: score(harness), range(20)))

    assert sorted(r.raw_score for r in results) == sorted(expected)
    assert len(harness.calls) == 20


CHECKS = [
    check_declares_valid_range,
    check_scores_at_range_points,
    check_integer_score_becomes_float,
    check_rejects_out_of_range_scores,
    check_rejects_non_numeric_scores,
    check_handles_empty_and_minimal_context,
    check_does_not_mutate_context,
    check_reports_stable_metadata,
    check_calls_backend_every_time,
    check_returns_fresh_scores,
    check_instances_do_not_share_state,
    check_accepts_positional_and_keyword_calls,
    check_verdict_counts,
    check_backend_errors_propagate,
    check_concurrent_calls,
]


# --------------------------------------------------
# The contract, run against every adapter
# --------------------------------------------------


@pytest.mark.parametrize("make", ADAPTERS.values(), ids=[cls.__name__ for cls in ADAPTERS])
@pytest.mark.parametrize("check", CHECKS, ids=[check.__name__ for check in CHECKS])
def test_adapter_meets_contract(check, make):
    check(make)


def in_judges_package(module: str) -> bool:
    """True for mtlj.judges itself (its __init__) and every module below it."""
    package = mtlj.judges.__name__
    return module == package or module.startswith(f"{package}.")


def concrete_judges() -> set[type[Judge]]:
    """Every non-abstract Judge subclass defined in the mtlj.judges package or its subpackages."""
    package = mtlj.judges.__name__
    for module in pkgutil.walk_packages(mtlj.judges.__path__, f"{package}."):
        importlib.import_module(module.name)

    found, pending = set(), [Judge]
    while pending:
        for sub in pending.pop().__subclasses__():
            pending.append(sub)
            if not inspect.isabstract(sub) and in_judges_package(sub.__module__):
                found.add(sub)
    return found


def test_every_adapter_is_registered():
    missing = concrete_judges() - set(ADAPTERS)

    assert not missing, f"Add a harness to ADAPTERS for: {sorted(c.__name__ for c in missing)}"


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("mtlj.judges", True),  # Defined in mtlj/judges/__init__.py
        ("mtlj.judges.callable", True),
        ("mtlj.judges.deepeval.faithfulness", True),  # Subpackage
        ("mtlj.judgesx", False),  # Similar name, different package
        ("mtlj.operators.base", False),
        ("test_contract", False),  # Test doubles like BrokenJudge
    ],
)
def test_registration_guard_covers_whole_package(module, expected):
    assert in_judges_package(module) is expected


def test_registration_guard_finds_callable_judge():
    assert CallableJudge in concrete_judges()


# --------------------------------------------------
# The contract catches broken adapters
# --------------------------------------------------


class BrokenJudge(Judge):
    """Base for deliberately broken adapters; the backend is the harness."""

    name = "broken"
    model = "broken-model"
    version = "1.0"
    score_range = (0.0, 1.0)

    def __init__(self, backend: Callable[..., Any]):
        self.backend = backend

    def score_raw(self, input: str, output: str, context: Context) -> float | RawScore:
        return self.backend(input, output, context)


class CachingJudge(BrokenJudge):
    """Remembers scores per input, shared across instances."""

    cache: ClassVar[dict] = {}

    def score_raw(self, input, output, context):
        key = (input, output)
        if key not in self.cache:
            self.cache[key] = self.backend(input, output, context)
        return self.cache[key]


class DriftingVersionJudge(BrokenJudge):
    """Reports a different version on every call."""

    def score_raw(self, input, output, context):
        self.version = f"{float(self.version) + 1}"
        return self.backend(input, output, context)


class KeywordOnlyJudge(BrokenJudge):
    """Takes keyword-only arguments, so Judge.__call__'s positional call breaks."""

    def score_raw(self, *, input, output, context):
        return self.backend(input, output, context)


class SwallowingJudge(BrokenJudge):
    """Hides backend failures behind a made-up score."""

    def score_raw(self, input, output, context):
        try:
            return self.backend(input, output, context)
        except BackendError:
            return 0.0


class ClampingJudge(BrokenJudge):
    """Forces any backend score into range instead of rejecting it."""

    def score_raw(self, input, output, context):
        raw = self.backend(input, output, context)
        return min(max(float(raw), 0.0), 1.0) if math.isfinite(float(raw)) else 0.0


class MutatingJudge(BrokenJudge):
    """Writes into the context it was given."""

    def score_raw(self, input, output, context):
        if isinstance(context, dict):
            context["seen"] = True
        return self.backend(input, output, context)


class FabricatingVerdictJudge(BrokenJudge):
    """Derives verdict counts from the score, which the 10-20 decision forbids."""

    def score_raw(self, input, output, context):
        raw = float(self.backend(input, output, context))
        yes = round(raw * 10)
        return RawScore(raw, verdict_counts=VerdictCounts(yes, 0, 10 - yes), n_claims=10)


class StubbornJudge(BrokenJudge):
    """Scores a constant once warmed up, so repeated calls never see a new answer."""

    def __init__(self, backend):
        super().__init__(backend)
        self.first = None

    def score_raw(self, input, output, context):
        raw = self.backend(input, output, context)
        if self.first is None:
            self.first = raw
        return self.first


class RacyJudge(BrokenJudge):
    """Keeps the in-flight score on the instance, so concurrent calls overwrite each other."""

    def score_raw(self, input, output, context):
        self.current = self.backend(input, output, context)
        time.sleep(0.01)
        return self.current


def broken(judge_cls: type[BrokenJudge], *, reports_verdicts: bool = True) -> Make:
    def make() -> Harness:
        CachingJudge.cache.clear()
        harness = Harness(reports_verdicts=reports_verdicts, errors=(BackendError,))
        harness.judge = judge_cls(harness.respond)
        return harness

    return make


@pytest.mark.parametrize(
    ("make", "check"),
    [
        (broken(CachingJudge), check_calls_backend_every_time),
        (broken(CachingJudge), check_instances_do_not_share_state),
        (broken(StubbornJudge), check_returns_fresh_scores),
        (broken(DriftingVersionJudge), check_reports_stable_metadata),
        (broken(KeywordOnlyJudge), check_accepts_positional_and_keyword_calls),
        (broken(SwallowingJudge), check_backend_errors_propagate),
        (broken(ClampingJudge), check_rejects_out_of_range_scores),
        (broken(MutatingJudge), check_does_not_mutate_context),
        (broken(FabricatingVerdictJudge, reports_verdicts=False), check_verdict_counts),
        (broken(RacyJudge), check_concurrent_calls),
    ],
    ids=[
        "caching-calls",
        "caching-shared-state",
        "stubborn",
        "drifting-version",
        "keyword-only",
        "swallowing-errors",
        "clamping",
        "mutating-context",
        "fabricating-verdicts",
        "racing-threads",
    ],
)
def test_contract_catches_broken_adapter(make, check):
    with pytest.raises(AssertionError):
        check(make)


@pytest.mark.parametrize("check", CHECKS, ids=[check.__name__ for check in CHECKS])
def test_well_behaved_broken_base_passes(check):
    """The broken judges differ from BrokenJudge only in their bug, so failures above are real."""
    check(broken(BrokenJudge))
