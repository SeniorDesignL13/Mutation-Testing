"""Wrap a plain Python callable as a judge."""

from __future__ import annotations

from collections.abc import Callable

from mtlj.judges.base import Context, Judge, RawScore

ScoreFn = Callable[[str, str, Context], float | RawScore]


class CallableJudge(Judge):
    """Adapt a Python function into the shared Judge interface.

    The wrapped function receives (input, output, context) and returns
    a raw numeric score within the declared score_range, or a RawScore
    carrying that score plus claim verdict counts.
    """

    def __init__(
        self,
        score_fn: ScoreFn,
        *,
        name: str = "callable-judge",
        model: str = "python-callable",
        version: str = "1.0.0",
        score_range: tuple[float, float] = (0.0, 1.0),
        higher_is_better: bool = True,
    ) -> None:
        if not callable(score_fn):
            raise TypeError("score_fn must be callable")

        self.score_fn = score_fn
        self.name = name
        self.model = model
        self.version = version
        # A list (e.g. from JSON config) would make every JudgeResult unhashable.
        self.score_range = tuple(score_range)
        self.higher_is_better = higher_is_better

    def score_raw(
        self,
        input: str,
        output: str,
        context: Context,
    ) -> float | RawScore:
        """Invoke the user function and return its raw score."""
        # Judge.__call__ converts and range-checks the score, so a RawScore passes through as is.
        return self.score_fn(input, output, context)
