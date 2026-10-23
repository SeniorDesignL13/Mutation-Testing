"""Framework-agnostic contract for LLM judges.

A judge evaluates an input/output pair with optional context and returns
a normalized score plus metadata identifying the judge and model.

## Creating a Custom Judge

Subclass Judge to integrate a custom scoring function into mtlj. Each judge provides a name,
underlying model, version, declared score range, and a method for normalizing raw scores to the
range [0.0, 1.0].

### Worked example

Suppose you have a judge that scores responses from 1 to 5, where higher scores indicate better
responses.

from dataclasses import dataclass

from mtlj.judges.base import Judge


@dataclass
class MyJudge(Judge):
    name: str = "MyJudge"
    model: str = "example-model"
    version: str = "1.0.0"
    score_range: tuple = (1.0, 5.0)

    def score_raw(self, input, output, context):
        return 4.0


### Using the judge

judge = MyJudge()

print(judge.name)  # MyJudge

print(judge.model)  # example-model

print(judge.version)  # 1.0.0

print(judge.score_range)  # (1.0, 5.0)

print(judge("q", "a", {}).score)  # 0.75


The raw score of 4.0 becomes 0.75 because it lies three-quarters of the way between the declared
minimum of 1.0 and maximum of 5.0.

### Reporting claim verdicts

A judge that rules on individual claims can return a RawScore instead of a float, so the
verdicts the score ignores are kept (see docs/metrics.md, "Decisions"):

    def score_raw(self, input, output, context):
        return RawScore(4.0, verdict_counts=VerdictCounts(yes=6, borderline=1, no=0), n_claims=7)

The counts may sum to less than n_claims, because some judges (DeepEval faithfulness) skip
a verdict for a claim now and then.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any, NamedTuple, final

ScoreRange = tuple[float, float]
Context = Mapping[str, Any]


class VerdictCounts(NamedTuple):
    """How many claims the judge ruled yes, borderline, and no."""

    yes: int
    borderline: int
    no: int


@dataclass(frozen=True, slots=True)
class RawScore:
    """A raw score plus the claim verdicts behind it, for judges that report them."""

    score: float
    verdict_counts: VerdictCounts | None = None
    n_claims: int | None = None


def _is_count(value: object) -> bool:
    """True for a non-negative int; bools are rejected even though they subclass int."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Standardized result returned by any judge."""

    score: float
    raw_score: float
    score_range: ScoreRange
    judge_name: str
    model: str
    version: str
    verdict_counts: VerdictCounts | None = None
    n_claims: int | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be finite and within [0.0, 1.0]")

        low, high = self.score_range
        if not isfinite(low) or not isfinite(high) or low >= high:
            raise ValueError("score_range must have finite bounds with low < high")

        if not isfinite(self.raw_score) or not low <= self.raw_score <= high:
            raise ValueError("raw_score must be finite and within score_range")

        if not all((self.judge_name, self.model, self.version)):
            raise ValueError("judge_name, model, and version are required")

        if self.n_claims is not None and not _is_count(self.n_claims):
            raise ValueError("n_claims must be a non-negative int")

        if self.verdict_counts is not None:
            if len(self.verdict_counts) != 3 or not all(map(_is_count, self.verdict_counts)):
                raise ValueError("verdict_counts must be three non-negative ints")
            if self.n_claims is None:
                raise ValueError("n_claims is required when verdict_counts is given")
            # Less than n_claims is allowed: DeepEval sometimes skips a verdict for a claim.
            if sum(self.verdict_counts) > self.n_claims:
                raise ValueError("verdict_counts must not sum to more than n_claims")


class Judge(ABC):
    """Base contract for framework-independent LLM judges.

    Implement score_raw() to integrate any local model, hosted LLM,
    evaluation framework, or deterministic test judge.
    """

    name: str
    model: str
    version: str
    score_range: ScoreRange
    higher_is_better: bool = True

    @abstractmethod
    def score_raw(self, input: str, output: str, context: Context) -> float | RawScore:
        """Return a score on this judge's declared score_range, optionally with verdicts."""
        ...  # Place holder

    @final
    def __call__(self, input: str, output: str, context: Context) -> JudgeResult:
        """Evaluate and return a normalized score with judge metadata."""
        low, high = self.score_range

        if not isfinite(low) or not isfinite(high) or low >= high:
            raise ValueError("score_range must have finite bounds with low < high")

        raw = self.score_raw(input, output, context)
        if not isinstance(raw, RawScore):
            raw = RawScore(raw)
        raw_score = float(raw.score)

        if not isfinite(raw_score) or not low <= raw_score <= high:
            raise ValueError(
                f"Judge {self.name!r} returned {raw_score}; "
                f"expected a finite score in [{low}, {high}]"
            )

        normalized = (raw_score - low) / (high - low)

        if not self.higher_is_better:
            normalized = 1.0 - normalized

        return JudgeResult(
            score=normalized,
            raw_score=raw_score,
            score_range=self.score_range,
            judge_name=self.name,
            model=self.model,
            version=self.version,
            verdict_counts=raw.verdict_counts,
            n_claims=raw.n_claims,
        )
