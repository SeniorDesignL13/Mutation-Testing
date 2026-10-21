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
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any, final

ScoreRange = tuple[float, float]
Context = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Standardized result returned by any judge."""

    score: float
    raw_score: float
    score_range: ScoreRange
    judge_name: str
    model: str
    version: str

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
    def score_raw(self, input: str, output: str, context: Context) -> float:
        """Return a score on this judge's declared score_range."""
        ...  # Place holder

    @final
    def __call__(self, input: str, output: str, context: Context) -> JudgeResult:
        """Evaluate and return a normalized score with judge metadata."""
        low, high = self.score_range

        if not isfinite(low) or not isfinite(high) or low >= high:
            raise ValueError("score_range must have finite bounds with low < high")

        raw_score = float(self.score_raw(input, output, context))

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
        )
