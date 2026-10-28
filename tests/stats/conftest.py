"""Fixtures shared by the stats tests."""

import csv
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import pytest

from mtlj.stats.detection import DetectionRate, MutantScores, detection_rate

SPIKE_SCORES = Path(__file__).parents[2] / "spike" / "scores.csv"

SpikeTexts = tuple[dict[str, list[float]], list[MutantScores]]


@pytest.fixture(scope="session")
def spike_texts() -> Callable[[str, str], SpikeTexts]:
    """Originals and one mutation type's mutants in the MTLJ-31 spike's scores, by judge config."""

    def texts(config: str, mutation_type: str) -> SpikeTexts:
        scores: dict[tuple[str, str], list[float]] = defaultdict(list)
        with SPIKE_SCORES.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["config"] == config:
                    scores[(row["record_id"], row["mutation_type"])].append(float(row["score"]))
        originals = {r: s for (r, kind), s in scores.items() if kind == "original"}
        mutants = [MutantScores(r, s) for (r, kind), s in scores.items() if kind == mutation_type]
        return originals, mutants

    return texts


@pytest.fixture(scope="session")
def spike_rate(
    spike_texts: Callable[[str, str], SpikeTexts],
) -> Callable[[str, str], DetectionRate]:
    """Detection rate of one mutation type in the MTLJ-31 spike's scores, by judge config."""

    def rate(config: str, mutation_type: str) -> DetectionRate:
        return detection_rate(*spike_texts(config, mutation_type))

    return rate
