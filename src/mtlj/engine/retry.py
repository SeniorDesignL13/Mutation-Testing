"""Retrying judge calls that fail for a reason that passes (MTLJ-94).

A multi-hour run makes tens of thousands of judge calls, and some fail for reasons that have
nothing to do with the text being scored: a timeout, a dropped connection, a rate limit, a
server error. ``is_transient`` sorts every failure into one of two kinds:

* **Transient**, worth another try: ``TimeoutError``, ``ConnectionError``, the OpenAI SDK's
  ``APIConnectionError`` and ``APITimeoutError``, httpx's timeouts and network errors, and an
  HTTP status that means "try later": 408, 409, 429 or 5xx (the statuses the OpenAI SDK retries).
* **Permanent**, where the same call would fail the same way: everything else. A bad API key
  (401), a bad request (400), a missing model (404), a score out of range (``ValueError``), a bug
  in the adapter. A 429 whose error code is ``insufficient_quota`` is permanent too: the account
  is out of credit, and waiting won't refill it.

SDK errors are recognized without importing the SDKs: by ``status_code`` on the exception or on
its ``response``, and by class name anywhere in the exception's class hierarchy. An exception
raised ``from`` another is judged by the first exception in that chain that says either way.
DeepEval, for one, raises tenacity's ``RetryError`` from the last error once its own retries run
out, so an OpenAI rate limit that outlasts DeepEval's retries is still seen as one. An adapter
whose backend raises something else can say a failure is transient by raising ``TimeoutError``
or ``ConnectionError`` from it.

Retry k (1 for the first) waits ``base_delay_s * multiplier ** (k - 1)`` seconds, at most
``max_delay_s``, less a random share of up to ``jitter`` so calls that failed together don't all
retry together. A ``Retry-After`` the server sent raises the wait to at least that, still at most
``max_delay_s``. With the defaults, a call is tried up to 6 times, with at most 31 seconds of
waiting between the tries (23 on average).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass

# What kind of failure a stored error was.
TRANSIENT = "transient"
PERMANENT = "permanent"

# Statuses that mean "try again later". The OpenAI SDK retries the same ones.
TRANSIENT_STATUSES = frozenset({408, 409, 429})
# Class names, anywhere in an exception's hierarchy, that mean a transient failure. These cover
# the OpenAI and Anthropic SDKs and httpx (which both SDKs use), and requests.
TRANSIENT_CLASS_NAMES = frozenset(
    {
        "APIConnectionError",  # openai, anthropic; APITimeoutError is a subclass
        "APITimeoutError",
        "TimeoutException",  # httpx: connect, read, write and pool timeouts
        "NetworkError",  # httpx: connect, read, write and close errors
        "RemoteProtocolError",  # httpx: the server closed the connection mid-response
        "ConnectionError",  # requests' own, which isn't the built-in one
        "Timeout",  # requests
        "ChunkedEncodingError",  # requests: the response was cut off
    }
)
# A rate-limit error code that means the account is out of credit, not that it's busy.
OUT_OF_QUOTA = "insufficient_quota"
# How far down a chain of ``raise ... from`` to look.
_MAX_CHAIN = 10


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """The exception, then what it was raised from (``__cause__``), and so on."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < _MAX_CHAIN:
        seen.add(id(current))
        yield current
        current = current.__cause__


def status_code(exc: BaseException) -> int | None:
    """The HTTP status of a failed request: ``exc.status_code``, or its ``response``'s."""
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    # A bool is an int, but no status.
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _out_of_quota(exc: BaseException) -> bool:
    """A rate limit that won't pass: the OpenAI SDK's ``code``, or failing that, the message."""
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code == OUT_OF_QUOTA
    message = str(exc).lower()
    return OUT_OF_QUOTA in message or "exceeded your current quota" in message


def _verdict(exc: BaseException) -> bool | None:
    """True for a transient failure, False for a permanent one, None if this one doesn't say."""
    status = status_code(exc)
    if status is not None:
        if status == 429 and _out_of_quota(exc):
            return False
        return status in TRANSIENT_STATUSES or status >= 500
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if any(cls.__name__ in TRANSIENT_CLASS_NAMES for cls in type(exc).__mro__):
        return True
    return None


def is_transient(exc: BaseException) -> bool:
    """Whether a failed judge call is worth trying again (see the module docstring)."""
    for link in _chain(exc):
        verdict = _verdict(link)
        if verdict is not None:
            return verdict
    return False


def _seconds(value: object) -> float | None:
    """A non-negative, finite number of seconds, or None."""
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None  # e.g. an HTTP date, which isn't worth parsing
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def retry_after(exc: BaseException) -> float | None:
    """How long the server asked to wait, in seconds, if it did.

    Read from a ``retry_after`` attribute, or the ``Retry-After`` or ``retry-after-ms`` header
    (OpenAI sends both) of the exception's ``response``, anywhere in its ``raise ... from`` chain.
    """
    for link in _chain(exc):
        seconds = _seconds(getattr(link, "retry_after", None))
        if seconds is not None:
            return seconds
        headers = getattr(getattr(link, "response", None), "headers", None)
        if headers is None or not hasattr(headers, "get"):
            continue
        millis = _seconds(headers.get("retry-after-ms") or headers.get("Retry-After-Ms"))
        if millis is not None:
            return millis / 1000
        seconds = _seconds(headers.get("retry-after") or headers.get("Retry-After"))
        if seconds is not None:
            return seconds
    return None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How a transient judge failure is retried.

    Args:
        max_attempts: calls in all, the first included. 1 never retries.
        base_delay_s: the wait before the first retry.
        multiplier: how much longer each wait is than the one before.
        max_delay_s: the longest any wait can be, a server's ``Retry-After`` included.
        jitter: the most each wait is cut by at random, as a share of it (0 to 1).
    """

    max_attempts: int = 6
    base_delay_s: float = 1.0
    multiplier: float = 2.0
    max_delay_s: float = 60.0
    jitter: float = 0.5

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise TypeError(f"max_attempts must be an int, got {self.max_attempts!r}")
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {self.max_attempts}")
        for name in ("base_delay_s", "max_delay_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0, got {value}")
        if not math.isfinite(self.multiplier) or self.multiplier < 1:
            raise ValueError(f"multiplier must be a finite number >= 1, got {self.multiplier}")
        if not 0 <= self.jitter <= 1:
            raise ValueError(f"jitter must be in [0, 1], got {self.jitter}")

    def delay(
        self,
        retry: int,
        exc: BaseException | None = None,
        *,
        rng: Callable[[], float] = random.random,
    ) -> float:
        """Seconds to wait before retry number ``retry`` (1 for the first), after ``exc``."""
        if retry < 1:
            raise ValueError(f"retry must be at least 1, got {retry}")
        if self.base_delay_s == 0:
            wait = 0.0
        else:
            try:
                wait = self.base_delay_s * self.multiplier ** (retry - 1)
            except OverflowError:
                wait = math.inf
        wait = min(wait, self.max_delay_s) * (1 - self.jitter * rng())
        asked = retry_after(exc) if exc is not None else None
        if asked is not None:
            wait = max(wait, min(asked, self.max_delay_s))
        return wait


# Retries up to 5 times, waiting at most 1, 2, 4, 8 and 16 seconds before them.
DEFAULT_RETRY = RetryPolicy()
# Records every failure at once.
NO_RETRY = RetryPolicy(max_attempts=1)
