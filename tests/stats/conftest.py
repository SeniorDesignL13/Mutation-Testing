"""Fixtures shared by the stats tests."""

import csv
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import pytest

from mtlj.stats.detection import DetectionRate, MutantScores, detection_rate

SPIKE_SCORES = Path(__file__).parents[2] / "spike" / "scores.csv"


@pytest.fixture(scope="session")
def spike_rate() -> Callable[[str, str], DetectionRate]:
    """Detection rate of one mutation type in the MTLJ-31 spike's scores, by judge config."""

    def rate(config: str, mutation_type: str) -> DetectionRate:
        scores: dict[tuple[str, str], list[float]] = defaultdict(list)
        with SPIKE_SCORES.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["config"] == config:
                    scores[(row["record_id"], row["mutation_type"])].append(float(row["score"]))
        originals = {r: s for (r, kind), s in scores.items() if kind == "original"}
        mutants = [MutantScores(r, s) for (r, kind), s in scores.items() if kind == mutation_type]
        return detection_rate(originals, mutants)

    return rate
