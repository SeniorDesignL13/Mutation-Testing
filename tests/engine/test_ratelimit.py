"""Tests for the rate limiter (MTLJ-96). A fake clock, so no test waits for real time to pass."""

import asyncio
import math
import time

import pytest

from mtlj.engine.ratelimit import RateLimiter, check_rate_limit


class FakeClock:
    """A clock that only moves when something sleeps on it, or a test moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)  # let other tasks run, as a real sleep would


def limiter(calls_per_minute: float = 120, clock: FakeClock | None = None) -> RateLimiter:
    clock = clock or FakeClock()
    return RateLimiter(calls_per_minute, clock=clock, sleep=clock.sleep)


# --------------------------------------------------
# Spacing
# --------------------------------------------------


async def test_the_first_call_starts_at_once_and_the_rest_are_evenly_spaced() -> None:
    clock = FakeClock()
    limit = limiter(120, clock)  # one every 0.5 s
    starts = []

    for _ in range(5):
        assert await limit.acquire()
        starts.append(clock.now)

    assert starts == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])
    assert limit.interval_s == 0.5


async def test_calls_already_spaced_out_never_wait() -> None:
    clock = FakeClock()
    limit = limiter(60, clock)

    for _ in range(3):
        assert await limit.acquire()
        clock.now += 5  # slower than the limit, as a slow judge is

    assert (limit.throttled, limit.waited_s) == (0, 0.0)


async def test_an_idle_limiter_doesnt_save_up_turns_for_a_burst() -> None:
    clock = FakeClock()
    limit = limiter(120, clock)
    await limit.acquire()
    clock.now = 100.0  # idle for 100 s: 200 turns' worth

    starts = []
    for _ in range(3):
        await limit.acquire()
        starts.append(clock.now)

    assert starts == pytest.approx([100.0, 100.5, 101.0])


async def test_a_fractional_limit_works() -> None:
    clock = FakeClock()
    limit = limiter(0.5, clock)  # one every 2 minutes

    await limit.acquire()
    await limit.acquire()

    assert clock.now == pytest.approx(120.0)


async def test_concurrent_callers_are_served_in_the_order_they_asked() -> None:
    clock = FakeClock()
    limit = limiter(60, clock)
    served: list[tuple[int, float]] = []

    async def call(n: int) -> None:
        await limit.acquire()
        served.append((n, clock.now))

    # Started in order; each asks before the next starts, since tasks run in creation order.
    await asyncio.gather(*(call(n) for n in range(6)))

    assert [n for n, _ in served] == list(range(6))
    assert [t for _, t in served] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])


async def test_waits_are_counted() -> None:
    clock = FakeClock()
    limit = limiter(60, clock)

    for _ in range(4):
        await limit.acquire()

    # The first is free; the other three wait 1 s each.
    assert limit.throttled == 3
    assert limit.waited_s == pytest.approx(3.0)


# --------------------------------------------------
# Stopping
# --------------------------------------------------


async def test_a_stopped_run_gets_no_turn_and_uses_none_up() -> None:
    clock = FakeClock()
    limit = limiter(60, clock)
    stopped = asyncio.Event()
    stopped.set()

    assert not await limit.acquire(stopped)
    # The turn wasn't taken: the next call still starts at once.
    assert await limit.acquire()
    assert clock.now == 0.0


async def test_stopping_ends_a_wait_at_once_with_real_time() -> None:
    limit = RateLimiter(1)  # one a minute, on the real clock
    stop = asyncio.Event()
    assert await limit.acquire(stop)

    waiting = asyncio.create_task(limit.acquire(stop))
    queued = asyncio.create_task(limit.acquire(stop))  # behind the first, for the lock
    await asyncio.sleep(0.05)
    started = time.perf_counter()
    stop.set()

    assert await asyncio.wait_for(asyncio.gather(waiting, queued), timeout=2) == [False, False]
    assert time.perf_counter() - started < 1


async def test_a_cancelled_wait_lets_the_next_caller_through() -> None:
    clock = FakeClock()
    limit = RateLimiter(1, clock=clock, sleep=asyncio.sleep)  # waits for real, so it can hang
    await limit.acquire()

    waiting = asyncio.create_task(limit.acquire())
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    clock.now = 60.0  # the next turn has come
    assert await asyncio.wait_for(limit.acquire(), timeout=1)


# --------------------------------------------------
# Checking the limit
# --------------------------------------------------


@pytest.mark.parametrize("value", [0, -1, -0.5, math.inf, math.nan])
def test_a_limit_must_be_finite_and_above_zero(value) -> None:
    with pytest.raises(ValueError, match="calls_per_minute must be a finite number"):
        RateLimiter(value)


@pytest.mark.parametrize("value", [True, "60", None])
def test_a_limit_must_be_a_number(value) -> None:
    with pytest.raises(TypeError, match="calls_per_minute must be a number"):
        RateLimiter(value)


def test_check_rate_limit_names_the_setting() -> None:
    assert check_rate_limit(30) == 30.0
    with pytest.raises(ValueError, match=r"^MyJudge\.default_rate_limit must be"):
        check_rate_limit(0, "MyJudge.default_rate_limit")
