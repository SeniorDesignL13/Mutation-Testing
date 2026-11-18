"""Keeping judge calls under a hosted API's rate limit (MTLJ-96).

A hosted judge (DeepEval on OpenAI, say) is limited in requests per minute, and a multi-hour
audit at full concurrency can go well past that. Retrying 429s (``mtlj.engine.retry``) recovers
from going over now and then, but every retry is a wasted request and a wait. A ``RateLimiter``
keeps the run under the limit in the first place.

Calls are spaced evenly, one every ``60 / calls_per_minute`` seconds, rather than let through in
a burst at the top of each minute: providers enforce per-minute limits over shorter windows
too, so 100 calls in the first second of a minute can be refused even under 100 a minute. A
limiter that was idle doesn't save up turns for a burst later, for the same reason.

The limit counts judge calls, not the requests a call makes to its backend. One DeepEval
faithfulness call makes about 3 OpenAI requests, so its limit is set at about a third of the
account's requests-per-minute limit (see ``Judge.default_rate_limit``).

Waiters are served in the order they asked: the limiter waits under an ``asyncio.Lock``, which
wakes waiters first come, first served, so no worker can be starved by the others.

A limiter belongs to one event loop and is used from that loop's thread only, which the runner
guarantees: workers are asyncio tasks, and only the judge calls themselves run on threads.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Awaitable, Callable

# Seconds in the unit the limit is given in.
_MINUTE = 60.0


def check_rate_limit(calls_per_minute: object, name: str = "rate_limit") -> float:
    """``calls_per_minute`` as a float, if it's a finite number > 0.

    Raises:
        TypeError: if it isn't a real number (a bool is rejected, though it's an int).
        ValueError: if it isn't finite and greater than 0.
    """
    if isinstance(calls_per_minute, bool) or not isinstance(calls_per_minute, (int, float)):
        raise TypeError(f"{name} must be a number of calls per minute, got {calls_per_minute!r}")
    value = float(calls_per_minute)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number of calls per minute > 0, got {value}")
    return value


class RateLimiter:
    """Lets at most ``calls_per_minute`` calls start a minute, evenly spaced.

    Args:
        calls_per_minute: the limit; any finite number > 0, fractions included.
        clock: monotonic seconds. Replaced in tests, with ``sleep``, so no real time passes.
        sleep: waits the given seconds.
    """

    def __init__(
        self,
        calls_per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.calls_per_minute = check_rate_limit(calls_per_minute, "calls_per_minute")
        self.interval_s = _MINUTE / self.calls_per_minute
        self._clock = clock
        self._sleep = sleep
        # The earliest the next call may start. -inf lets the first call start at once.
        self._next = -math.inf
        self._lock = asyncio.Lock()
        # How long calls waited for their turn, added up over every call (so with several
        # workers waiting at once it can be more than the run's elapsed time), and how many
        # calls had to wait at all.
        self.waited_s = 0.0
        self.throttled = 0

    async def acquire(self, stop: asyncio.Event | None = None) -> bool:
        """Wait for this call's turn. True when it may start.

        False, at once and without taking a turn, once ``stop`` is set: when it is set before
        the call's turn comes, while it waits, or while other calls are ahead of it.
        """
        asked = self._clock()
        paused = False
        async with self._lock:
            while True:
                if stop is not None and stop.is_set():
                    return False
                now = self._clock()
                wait = self._next - now
                if wait <= 0:
                    break
                paused = True
                await self._pause(wait, stop)
            # From now, not from the last turn: an idle limiter doesn't save turns for a burst.
            self._next = now + self.interval_s
        if paused:
            # Includes any time spent queued behind other calls for the lock.
            self.waited_s += now - asked
            self.throttled += 1
        return True

    async def _pause(self, seconds: float, stop: asyncio.Event | None) -> None:
        """Sleep for ``seconds``, or until ``stop`` is set."""
        if stop is None:
            await self._sleep(seconds)
            return
        sleeping = asyncio.ensure_future(self._sleep(seconds))
        stopping = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({sleeping, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in (sleeping, stopping):
                waiter.cancel()
            for waiter in (sleeping, stopping):
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter
