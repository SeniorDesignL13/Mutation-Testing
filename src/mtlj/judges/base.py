"""Framework-agnostic contract for LLM judges.

A judge evaluates an input/output pair with optional context and returns
a normalized score plus metadata identifying the judge and model.

Scores are normalized to [0.0, 1.0], where 1.0 always represents
the better outcome. The original score and declared range are preserved.
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


def normalize_score(
    score: float,
    min_score: float,
    max_score: float,
) -> float:
    if max_score <= min_score:
        raise ValueError("max_score must be greater than min_score")

    normalized = (score - min_score) / (max_score - min_score)

    return max(0.0, min(1.0, float(normalized)))
