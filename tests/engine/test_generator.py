"""Tests for mutant generation over a corpus."""

import json
import logging
from pathlib import Path
from typing import ClassVar

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.generator import (
    NO_CHANGE,
    OPERATOR_ERROR,
    REPEATED_DOSE,
    BatteryItem,
    default_battery,
    derive_seed,
    generate_mutants,
    run_manifest,
    write_generation,
)
from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)


def _record(n: int) -> DatasetRecord:
    return DatasetRecord(
        record_id=f"t_{n}",
        source_dataset="test",
        question="Where was the conference held?",
        context=[{"passage_id": "p1", "text": "The annual conference took place in Paris."}],
        answer=f"The conference number {n} was held in Paris last spring.",
    )


RECORDS = [_record(n) for n in range(3)]


def _meta(name: str) -> OperatorMetadata:
    return OperatorMetadata(
        name=name,
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description="test operator",
    )


class AppendSeed(MutationOperator):
    """Appends its seed to the text, so the output depends on the seed."""

    metadata = _meta("append_seed")

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        return MutationResult(text, f"{text} [{seed}]", True, {"intensity": intensity})


class NeverApplies(MutationOperator):
    """Can't apply to any record: returns the text unchanged."""

    metadata = _meta("never_applies")

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        return MutationResult(text, text, False, {"skipped": {"no_target": 1}})


class FailsOnRecordOne(MutationOperator):
    """Raises on one record and works on the others."""

    metadata = _meta("fails_on_one")

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        if "number 1 " in text:
            raise RuntimeError("boom")
        return MutationResult(text, text + "!", True, {})


class FixedDose(MutationOperator):
    """Reports how many substitutions it made, like entity swap: 1, 1, then 3 by intensity."""

    metadata = _meta("fixed_dose")
    DOSES: ClassVar[dict[float, int]] = {0.2: 1, 0.6: 1, 1.0: 3}

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        count = self.DOSES[intensity]
        subs = [{"original": f"x{i}", "replacement": f"y{i}"} for i in range(count)]
        return MutationResult(text, f"{text} [{seed}]", True, {"substitutions": subs})


def test_one_mutant_per_record_operator_and_intensity() -> None:
    battery = [BatteryItem(AppendSeed(), (0.25, 0.5, 1.0))]
    result = generate_mutants(RECORDS, battery)
    assert len(result.mutants) == len(RECORDS) * 3
    assert not result.skipped
    assert len({m.mutant_id for m in result.mutants}) == len(result.mutants)


def test_originals_are_kept_and_pair_with_their_mutants() -> None:
    result = generate_mutants(RECORDS, [BatteryItem(AppendSeed(), (0.5, 1.0))])
    assert result.originals == RECORDS
    by_id = {r.record_id: r for r in result.originals}
    for mutant in result.mutants:
        assert mutant.original_text == by_id[mutant.record_id].answer
        assert mutant.mutated_text != mutant.original_text


def test_full_provenance_on_every_mutant() -> None:
    result = generate_mutants(RECORDS, [BatteryItem(AppendSeed(), (0.5,))], base_seed=9)
    for mutant in result.mutants:
        assert mutant.operator_name == "append_seed"
        assert mutant.operator_family is OperatorFamily.DEGRADING
        assert mutant.expected_direction is ExpectedDirection.DECREASE
        assert mutant.intensity == 0.5
        assert mutant.seed == derive_seed(9, mutant.record_id, "append_seed", 0.5)
        assert mutant.context_sha256


def test_operator_that_changes_nothing_is_skipped_and_logged() -> None:
    battery = [BatteryItem(NeverApplies(), (1.0,)), BatteryItem(AppendSeed(), (1.0,))]
    result = generate_mutants(RECORDS, battery)
    assert len(result.mutants) == len(RECORDS)  # the other operator still ran
    assert len(result.skipped) == len(RECORDS)
    skip = result.skipped[0]
    assert skip.reason == NO_CHANGE
    assert skip.operator_name == "never_applies"
    assert skip.detail == {"no_target": 1}  # the operator's own reason is kept


def test_operator_that_raises_is_skipped_and_the_run_continues(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="mtlj.engine.generator"):
        result = generate_mutants(RECORDS, [BatteryItem(FailsOnRecordOne(), (1.0,))])
    assert [m.record_id for m in result.mutants] == ["t_0", "t_2"]
    assert len(result.skipped) == 1
    skip = result.skipped[0]
    assert (skip.record_id, skip.reason) == ("t_1", OPERATOR_ERROR)
    assert "boom" in skip.detail["error"]
    assert "t_1" in caplog.text


def test_same_base_seed_is_reproducible() -> None:
    battery = [BatteryItem(AppendSeed(), (0.5, 1.0))]
    a = generate_mutants(RECORDS, battery, base_seed=3)
    b = generate_mutants(RECORDS, battery, base_seed=3)
    c = generate_mutants(RECORDS, battery, base_seed=4)
    assert a.mutants == b.mutants
    assert [m.mutant_id for m in a.mutants] != [m.mutant_id for m in c.mutants]


def test_seeds_are_stable_and_differ_across_records() -> None:
    assert derive_seed(0, "t_1", "entity_swap", 0.5) == derive_seed(0, "t_1", "entity_swap", 0.5)
    seeds = {derive_seed(0, f"t_{n}", "entity_swap", 0.5) for n in range(50)}
    assert len(seeds) == 50
    assert all(0 <= s < 2**32 for s in seeds)
    assert derive_seed(0, "t_1", "entity_swap", 0.5) != derive_seed(0, "t_1", "padding", 0.5)
    assert derive_seed(0, "t_1", "entity_swap", 0.5) != derive_seed(0, "t_1", "entity_swap", 1.0)


@pytest.mark.parametrize(
    ("battery", "message"),
    [
        ([], "empty"),
        ([BatteryItem(AppendSeed(), ())], "no intensities"),
        ([BatteryItem(AppendSeed(), (1.5,))], "[0, 1]"),
        ([BatteryItem(AppendSeed(), (-0.1,))], "[0, 1]"),
        ([BatteryItem(AppendSeed(), (0.5, 0.5))], "twice"),
        ([BatteryItem(AppendSeed(), (0.5,)), BatteryItem(AppendSeed(), (0.5,))], "twice"),
    ],
)
def test_bad_battery_is_rejected_before_anything_runs(battery, message) -> None:
    with pytest.raises(ValueError, match=message):
        generate_mutants(RECORDS, battery)


def test_duplicate_record_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate record_id"):
        generate_mutants([RECORDS[0], RECORDS[0]], [BatteryItem(AppendSeed(), (1.0,))])


def test_no_records_gives_an_empty_result() -> None:
    result = generate_mutants([], [BatteryItem(AppendSeed(), (1.0,))])
    assert (result.originals, result.mutants, result.skipped) == ([], [], [])


def test_summary_counts() -> None:
    battery = [BatteryItem(NeverApplies(), (0.5, 1.0)), BatteryItem(AppendSeed(), (1.0,))]
    summary = generate_mutants(RECORDS, battery).summary()
    assert summary == {
        "records": 3,
        "mutants": 3,
        "skipped": 6,
        "skipped_by_reason": {NO_CHANGE: 6},
        "mutants_by_operator": {"append_seed": 3},
    }


def test_default_battery_runs_the_real_operators_and_regenerates() -> None:
    result = generate_mutants(RECORDS, default_battery(), base_seed=1)
    padding = [m for m in result.mutants if m.operator_name == "verbosity_padding"]
    swaps = [m for m in result.mutants if m.operator_name == "entity_swap"]
    assert len(padding) == len(RECORDS) * 4
    assert swaps  # Paris is in the context, so at least one swap applies
    assert all(m.expected_direction is ExpectedDirection.UNCHANGED for m in padding)
    assert all(m.expected_direction is ExpectedDirection.DECREASE for m in swaps)
    by_id = {r.record_id: r for r in result.originals}
    for mutant in result.mutants:
        assert mutant.regenerate(by_id[mutant.record_id]) == mutant


def test_write_generation_writes_three_readable_files(tmp_path: Path) -> None:
    battery = [BatteryItem(AppendSeed(), (1.0,)), BatteryItem(NeverApplies(), (1.0,))]
    result = generate_mutants(RECORDS, battery)
    out = tmp_path / "run"
    write_generation(result, out)

    originals = [
        DatasetRecord.model_validate_json(line)
        for line in (out / "originals.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    mutants = [
        Mutant.model_validate_json(line)
        for line in (out / "mutants.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    skipped = [
        json.loads(line)
        for line in (out / "skipped.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert originals == result.originals
    assert mutants == result.mutants
    assert len(skipped) == len(RECORDS)
    assert {s["reason"] for s in skipped} == {NO_CHANGE}
    assert b"\r\n" not in (out / "mutants.jsonl").read_bytes()


def test_repeated_dose_is_skipped_and_the_lowest_intensity_is_kept() -> None:
    result = generate_mutants(RECORDS, [BatteryItem(FixedDose(), (0.2, 0.6, 1.0))])
    kept = {(m.record_id, m.intensity) for m in result.mutants}
    assert kept == {(r.record_id, i) for r in RECORDS for i in (0.2, 1.0)}
    assert len(result.skipped) == len(RECORDS)
    skip = result.skipped[0]
    assert (skip.intensity, skip.reason) == (0.6, REPEATED_DOSE)
    assert skip.detail == {"dose": 1, "same_as_intensity": 0.2}
    assert result.summary()["skipped_by_reason"] == {REPEATED_DOSE: len(RECORDS)}


def test_repeated_doses_can_be_kept() -> None:
    battery = [BatteryItem(FixedDose(), (0.2, 0.6, 1.0))]
    result = generate_mutants(RECORDS, battery, drop_repeated_doses=False)
    assert len(result.mutants) == len(RECORDS) * 3
    assert not result.skipped
    assert result.drop_repeated_doses is False


def test_operators_without_a_dose_are_never_deduplicated() -> None:
    result = generate_mutants(RECORDS, [BatteryItem(AppendSeed(), (0.25, 0.5, 1.0))])
    assert len(result.mutants) == len(RECORDS) * 3
    assert not result.skipped


def test_run_json_describes_the_run(tmp_path: Path) -> None:
    battery = [BatteryItem(FixedDose(), (0.2, 0.6, 1.0)), BatteryItem(AppendSeed(), (1.0,))]
    result = generate_mutants(RECORDS, battery, base_seed=7)
    write_generation(result, tmp_path)

    raw = (tmp_path / "run.json").read_bytes()
    assert b"\r\n" not in raw
    manifest = json.loads(raw)
    assert manifest == run_manifest(result)
    assert manifest["base_seed"] == 7
    assert manifest["battery"] == {"fixed_dose": [0.2, 0.6, 1.0], "append_seed": [1.0]}
    assert manifest["drop_repeated_doses"] is True
    assert manifest["summary"] == result.summary()
    assert set(manifest["versions"]) == {"python", "spacy", "en_core_web_sm"}


def test_run_json_is_the_same_for_the_same_run(tmp_path: Path) -> None:
    battery = [BatteryItem(AppendSeed(), (0.5, 1.0))]
    for name in ("a", "b"):
        write_generation(generate_mutants(RECORDS, battery, base_seed=2), tmp_path / name)
    assert (tmp_path / "a" / "run.json").read_bytes() == (tmp_path / "b" / "run.json").read_bytes()
