"""scripts/record_fixtures.py, run with a fake judge (MTLJ-90): no network, no cost."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.fixtures import FixtureStore, save_inputs
from mtlj.judges.base import RawScore, VerdictCounts
from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_fixtures.py"
COST = 0.001  # what each fake call reports


class Shout(MutationOperator):
    metadata = OperatorMetadata(
        name="shout",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description="test operator",
    )

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        return MutationResult(text, text + "!!!", True, {})


def _record(n: int) -> DatasetRecord:
    return DatasetRecord(
        record_id=f"t_{n}",
        source_dataset="test",
        question=f"Where was conference {n} held?",
        context=[{"passage_id": "p1", "text": f"Conference {n} took place in Paris."}],
        answer=f"Conference {n} was held in Paris.",
    )


RECORDS = [_record(n) for n in range(2)]
MUTANTS = [Mutant.generate(r, Shout(), 1.0, seed=7) for r in RECORDS]
SCORINGS = 5 * (len(RECORDS) + len(MUTANTS))  # the script's default 5 samples


@pytest.fixture
def recorder(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("record_fixtures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def fake_measure(input, output, passages, *, model, on_cost):
        calls.append(output)
        on_cost(COST)
        return RawScore(0.4 if output.endswith("!!!") else 0.9, VerdictCounts(1, 0, 0), 1)

    monkeypatch.setattr(module, "measure_faithfulness", fake_measure)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    save_inputs(tmp_path / "inputs", RECORDS, MUTANTS)
    out = tmp_path / "judge.jsonl"

    def run(*args: str) -> int:
        return module.main(["--out", str(out), "--inputs", str(tmp_path / "inputs"), *args])

    return run, out, calls


def test_a_complete_recording_writes_the_fixture_file(recorder, capsys):
    run, out, calls = recorder

    assert run("--yes") == 0

    assert len(calls) == SCORINGS
    assert len(FixtureStore(out, mode="replay")) == SCORINGS
    assert not out.with_name(out.name + ".partial").exists()
    assert "holds everything the replay test needs" in capsys.readouterr().out


def test_a_recording_stopped_by_the_cap_leaves_the_file_alone_and_resumes(recorder, capsys):
    run, out, calls = recorder

    # A cap of 3 calls' cost: the rest are refused.
    assert run("--yes", "--max-cost", str(3 * COST), "--max-concurrency", "1") == 1

    printed = capsys.readouterr().out
    assert not out.exists()  # nothing half-recorded reaches the committed file
    assert out.with_name(out.name + ".partial").exists()
    assert "spending cap" in printed
    assert "unchanged" in printed
    paid = len(calls)

    # The next run pays only for what's missing, then writes the whole file.
    assert run("--yes") == 0
    assert len(calls) - paid == SCORINGS - paid
    assert len(FixtureStore(out, mode="replay")) == SCORINGS


def test_a_trial_never_writes_the_file(recorder):
    run, out, _ = recorder

    assert run("--yes", "--limit", "1", "--samples", "1") == 1

    assert not out.exists()
    assert out.with_name(out.name + ".partial").exists()
