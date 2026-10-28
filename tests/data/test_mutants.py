"""Tests for the mutant format and its reproducibility."""

import copy
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mtlj.data.mutants import Mutant, context_sha256, make_mutant_id
from mtlj.data.records import DatasetRecord
from mtlj.operators.base import ExpectedDirection, MutationResult, OperatorFamily
from mtlj.operators.degrading.entity_swap import EntitySwapOperator
from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator

RECORD = DatasetRecord(
    record_id="t_1",
    source_dataset="test",
    question="Where was the conference held?",
    context=[{"passage_id": "p1", "text": "The annual conference took place in Paris."}],
    answer="The conference was held in Paris last spring.",
)

RECORD_CONTEXT_SHA256 = context_sha256([p.text for p in RECORD.context])


def _with_other_context() -> DatasetRecord:
    other = [{"passage_id": "p1", "text": "The summit took place in Rome."}]
    return DatasetRecord.model_validate({**RECORD.model_dump(), "context": other})


OPERATORS = [EntitySwapOperator(), VerbosityPaddingOperator()]

EXAMPLES = [
    DatasetRecord.model_validate_json(line)
    for line in (Path(__file__).parent / "example_records.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
]


def _mutant() -> Mutant:
    return Mutant.generate(RECORD, EntitySwapOperator(), intensity=1.0, seed=7)


def _id(**overrides) -> str:
    args = {
        "record_id": "t_1",
        "original_text": "A.",
        "context_sha256": context_sha256(["Some passage."]),
        "operator_name": "entity_swap",
        "intensity": 1.0,
        "parameters": {},
        "seed": 7,
    }
    return make_mutant_id(**{**args, **overrides})


# Reproducibility


@pytest.mark.parametrize("op", OPERATORS, ids=lambda op: op.metadata.name)
def test_same_seed_regenerates_byte_identical_mutant(op) -> None:
    mutant = Mutant.generate(RECORD, op, intensity=0.5, seed=11)
    again = mutant.regenerate(RECORD)
    assert again.mutated_text.encode("utf-8") == mutant.mutated_text.encode("utf-8")
    assert again.mutated_sha256 == mutant.mutated_sha256
    assert again == mutant


@pytest.mark.parametrize("record", EXAMPLES, ids=lambda r: r.record_id)
@pytest.mark.parametrize("op", OPERATORS, ids=lambda op: op.metadata.name)
@pytest.mark.parametrize("seed", [0, 1, 42, 2**31 - 1])
def test_every_example_regenerates_identically(record, op, seed) -> None:
    mutant = Mutant.generate(record, op, intensity=1.0, seed=seed)
    assert mutant.regenerate(record) == mutant


def test_regenerates_identically_after_json_round_trip() -> None:
    mutant = Mutant.model_validate_json(_mutant().model_dump_json())
    assert mutant.regenerate(RECORD) == mutant


def test_regeneration_is_identical_across_processes() -> None:
    # Python randomises str hashing per process; a mutant must not depend on it.
    script = (
        "import sys\n"
        "from mtlj.data.mutants import Mutant\n"
        "from mtlj.data.records import DatasetRecord\n"
        "from mtlj.operators.degrading.entity_swap import EntitySwapOperator\n"
        "from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator\n"
        "record = DatasetRecord.model_validate_json(sys.argv[1])\n"
        "for op in (EntitySwapOperator(), VerbosityPaddingOperator()):\n"
        "    print(Mutant.generate(record, op, intensity=0.7, seed=13).model_dump_json())\n"
    )
    outputs = []
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        result = subprocess.run(
            [sys.executable, "-c", script, RECORD.model_dump_json()],
            capture_output=True,
            check=True,
            env=env,
            text=True,
            encoding="utf-8",
        )
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    here = [
        Mutant.generate(RECORD, op, intensity=0.7, seed=13).model_dump_json() for op in OPERATORS
    ]
    assert [json.loads(line) for line in outputs[0].splitlines()] == [json.loads(h) for h in here]


def test_different_seeds_can_give_different_mutants() -> None:
    texts = {
        Mutant.generate(RECORD, EntitySwapOperator(), intensity=1.0, seed=s).mutated_text
        for s in range(20)
    }
    assert len(texts) > 1


def test_regenerate_exposes_a_non_reproducible_operator(monkeypatch) -> None:
    calls = iter(range(100))

    def drifting_apply(self, text, context, intensity=1.0, seed=None):
        return MutationResult(text, f"{text} {next(calls)}", True, {})

    monkeypatch.setattr(EntitySwapOperator, "apply", drifting_apply)
    mutant = Mutant.generate(RECORD, EntitySwapOperator(), intensity=1.0, seed=7)
    again = mutant.regenerate(RECORD)
    assert again.mutant_id == mutant.mutant_id
    assert again.mutated_sha256 != mutant.mutated_sha256
    assert again != mutant


def test_regenerate_rejects_other_record() -> None:
    other = RECORD.model_copy(update={"record_id": "t_2"})
    with pytest.raises(ValueError, match="t_1"):
        _mutant().regenerate(other)


def test_regenerate_rejects_changed_answer() -> None:
    corrected = RECORD.model_copy(update={"answer": "The conference was held in Lyon."})
    with pytest.raises(ValueError, match="answer has changed"):
        _mutant().regenerate(corrected)


def test_from_result_rejects_result_from_other_text() -> None:
    result = EntitySwapOperator().apply(
        "The meeting was held in Berlin.", [p.text for p in RECORD.context], seed=7
    )
    with pytest.raises(ValueError, match="t_1"):
        Mutant.from_result(RECORD, EntitySwapOperator(), result, intensity=1.0, seed=7)


def test_regenerate_rejects_changed_context() -> None:
    mutant = Mutant.generate(RECORD, VerbosityPaddingOperator(), intensity=0.5, seed=11)
    corrected = _with_other_context()
    with pytest.raises(ValueError, match="context has changed"):
        mutant.regenerate(corrected)


def test_same_answer_with_different_context_gets_a_different_id() -> None:
    """Padding builds its filler from the context, so the context is part of the mutant's id."""
    other = _with_other_context()
    first = Mutant.generate(RECORD, VerbosityPaddingOperator(), intensity=0.5, seed=11)
    second = Mutant.generate(other, VerbosityPaddingOperator(), intensity=0.5, seed=11)

    assert first.mutant_id != second.mutant_id
    assert first.context_sha256 != second.context_sha256


def test_context_hash_depends_on_passage_order() -> None:
    assert context_sha256(["a", "b"]) != context_sha256(["b", "a"])
    assert context_sha256(["ab"]) != context_sha256(["a", "b"])


def test_regenerate_rejects_unknown_operator() -> None:
    data = _mutant().model_dump()
    data["operator_name"] = "no_such_operator"
    data["mutant_id"] = _id(
        original_text=RECORD.answer,
        context_sha256=RECORD_CONTEXT_SHA256,
        operator_name="no_such_operator",
    )
    with pytest.raises(ValueError, match="no_such_operator"):
        Mutant.model_validate(data).regenerate(RECORD)


# What a mutant records


def test_entity_swap_records_what_was_swapped() -> None:
    mutant = _mutant()
    assert mutant.changed
    assert mutant.original_text == RECORD.answer
    assert mutant.details["swapped_from"] == "Paris"
    assert mutant.details["swapped_to"] in mutant.mutated_text


def test_direction_and_family_come_from_operator() -> None:
    swap = Mutant.generate(RECORD, EntitySwapOperator(), intensity=1.0, seed=1)
    pad = Mutant.generate(RECORD, VerbosityPaddingOperator(), intensity=1.0, seed=1)
    assert swap.expected_direction is ExpectedDirection.DECREASE
    assert swap.operator_family is OperatorFamily.DEGRADING
    assert pad.expected_direction is ExpectedDirection.UNCHANGED
    assert pad.operator_family is OperatorFamily.PRESERVING


def test_padding_uses_record_context() -> None:
    mutant = Mutant.generate(RECORD, VerbosityPaddingOperator(), intensity=1.0, seed=3)
    assert mutant.mutated_text.startswith(RECORD.answer)
    assert "conference" in mutant.mutated_text[len(RECORD.answer) :]


def test_unchanged_mutant_is_still_valid() -> None:
    record = RECORD.model_copy(update={"answer": "The soup needs more salt."})
    mutant = Mutant.generate(record, EntitySwapOperator(), intensity=1.0, seed=1)
    assert not mutant.changed
    assert mutant.mutated_text == mutant.original_text
    assert mutant.regenerate(record) == mutant


def test_changed_comes_from_the_texts_not_the_operator(monkeypatch) -> None:
    def misreporting_apply(self, text, context, intensity=1.0, seed=None):
        return MutationResult(text, text + " ", False, {})

    monkeypatch.setattr(EntitySwapOperator, "apply", misreporting_apply)
    assert _mutant().changed


def test_unicode_text_hashes_consistently() -> None:
    record = RECORD.model_copy(update={"answer": "Le café était à Paris — très bien."})
    mutant = Mutant.generate(record, VerbosityPaddingOperator(), intensity=0.2, seed=5)
    assert Mutant.model_validate_json(mutant.model_dump_json()) == mutant
    assert mutant.regenerate(record).mutated_sha256 == mutant.mutated_sha256


def test_round_trips_through_json() -> None:
    mutant = _mutant()
    data = mutant.model_dump_json()
    assert '"expected_direction":"decrease"' in data
    assert Mutant.model_validate_json(data) == mutant


def test_round_trips_through_python_dict() -> None:
    mutant = _mutant()
    dumped = mutant.model_dump()
    assert type(dumped["details"]) is dict
    assert Mutant.model_validate(dumped) == mutant


# Mutant id


def test_mutant_id_is_deterministic() -> None:
    assert _id() == _id()


@pytest.mark.parametrize(
    "change",
    [
        {"record_id": "t_2"},
        {"original_text": "B."},
        {"context_sha256": context_sha256(["Another passage."])},
        {"operator_name": "verbosity_padding"},
        {"intensity": 0.5},
        {"parameters": {"num_swaps": 1}},
        {"seed": 8},
    ],
    ids=lambda c: next(iter(c)),
)
def test_mutant_id_changes_with_each_input(change) -> None:
    assert _id(**change) != _id()


def test_mutant_id_normalises_numbers() -> None:
    assert _id(intensity=1) == _id(intensity=1.0)
    assert _id(parameters={"n": 1}) == _id(parameters={"n": 1.0})


def test_mutant_id_ignores_parameter_order() -> None:
    assert _id(parameters={"a": 1, "b": 2}) == _id(parameters={"b": 2, "a": 1})


def test_integer_intensity_is_accepted() -> None:
    as_int = Mutant.generate(RECORD, EntitySwapOperator(), intensity=1, seed=7)
    assert as_int == _mutant()
    assert as_int.regenerate(RECORD) == as_int


# Validation


def _apply_must_not_run(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("operator ran on invalid input")

    monkeypatch.setattr(VerbosityPaddingOperator, "apply", fail)


@pytest.mark.parametrize("intensity", [0.0, 1.0])
def test_intensity_bounds_accepted(intensity) -> None:
    mutant = Mutant.generate(RECORD, VerbosityPaddingOperator(), intensity=intensity, seed=1)
    assert mutant.intensity == intensity
    assert mutant.regenerate(RECORD) == mutant


@pytest.mark.parametrize("intensity", [-0.1, 1.01, 5.0])
def test_intensity_outside_bounds_rejected_before_operator_runs(intensity, monkeypatch) -> None:
    _apply_must_not_run(monkeypatch)
    with pytest.raises(ValueError, match="intensity"):
        Mutant.generate(RECORD, VerbosityPaddingOperator(), intensity=intensity, seed=1)


def test_intensity_out_of_range_rejected_on_load() -> None:
    data = _mutant().model_dump()
    data["intensity"] = 1.5
    with pytest.raises(ValidationError):
        Mutant.model_validate(data)


def test_parameters_rejected_before_operator_runs(monkeypatch) -> None:
    # apply() can't take parameters yet, so recording one would claim a setting never applied.
    _apply_must_not_run(monkeypatch)
    with pytest.raises(ValueError, match="parameters"):
        Mutant.generate(
            RECORD, VerbosityPaddingOperator(), intensity=1.0, seed=1, parameters={"words": 9}
        )


def test_parameters_rejected_on_load() -> None:
    data = _mutant().model_dump()
    data["parameters"] = {"num_swaps": 2.0}
    data["mutant_id"] = _id(
        original_text=RECORD.answer,
        context_sha256=RECORD_CONTEXT_SHA256,
        parameters={"num_swaps": 2.0},
    )
    with pytest.raises(ValidationError, match="parameters"):
        Mutant.model_validate(data)


def test_seed_is_required() -> None:
    data = _mutant().model_dump()
    data["seed"] = None
    with pytest.raises(ValidationError):
        Mutant.model_validate(data)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [("expected_direction", "unchanged"), ("operator_family", "preserving")],
)
def test_direction_and_family_must_match_a_known_operator(field, wrong) -> None:
    """Neither is part of the id, so a hand-edited row would otherwise flip detection."""
    data = _mutant().model_dump(mode="json")
    data[field] = wrong
    with pytest.raises(ValidationError, match="entity_swap is degrading / decrease"):
        Mutant.model_validate(data)


def test_unknown_direction_rejected() -> None:
    data = _mutant().model_dump(mode="json")
    data["expected_direction"] = "increase"
    with pytest.raises(ValidationError):
        Mutant.model_validate(data)


@pytest.mark.parametrize("field", ["mutant_id", "record_id", "operator_name", "mutated_sha256"])
def test_required_fields_missing_rejected(field) -> None:
    data = _mutant().model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        Mutant.model_validate(data)


def test_extra_fields_rejected() -> None:
    data = _mutant().model_dump()
    data["unexpected"] = "x"
    with pytest.raises(ValidationError):
        Mutant.model_validate(data)


def test_edited_mutated_text_rejected() -> None:
    data = _mutant().model_dump()
    data["mutated_text"] = "Something else."
    with pytest.raises(ValidationError, match="mutated_sha256"):
        Mutant.model_validate(data)


def test_edited_original_text_rejected() -> None:
    data = _mutant().model_dump()
    data["original_text"] = "Something else."
    with pytest.raises(ValidationError, match="mutant_id"):
        Mutant.model_validate(data)


def test_edited_seed_rejected() -> None:
    data = _mutant().model_dump()
    data["seed"] = 8
    with pytest.raises(ValidationError, match="mutant_id"):
        Mutant.model_validate(data)


def test_inconsistent_changed_flag_rejected() -> None:
    data = _mutant().model_dump()
    data["changed"] = False
    with pytest.raises(ValidationError, match="changed"):
        Mutant.model_validate(data)


# Immutability


def test_fields_cannot_be_reassigned() -> None:
    with pytest.raises(ValidationError):
        _mutant().seed = 8


@pytest.mark.parametrize("field", ["parameters", "details"])
def test_mappings_cannot_be_edited_in_place(field) -> None:
    with pytest.raises(TypeError):
        getattr(_mutant(), field)["num_swaps"] = 3


@pytest.mark.parametrize(
    "edit",
    [
        lambda m: m.__setitem__("k", 1),
        lambda m: m.__delitem__("swapped_from"),
        lambda m: m.update(k=1),
        lambda m: m.pop("swapped_from"),
        lambda m: m.popitem(),
        lambda m: m.clear(),
        lambda m: m.setdefault("k", 1),
        lambda m: m.__ior__({"k": 1}),
    ],
    ids=["setitem", "delitem", "update", "pop", "popitem", "clear", "setdefault", "ior"],
)
def test_every_in_place_edit_is_rejected(edit) -> None:
    mutant = _mutant()
    before = dict(mutant.details)
    with pytest.raises(TypeError, match="read-only"):
        edit(mutant.details)
    assert dict(mutant.details) == before


# Copying and pickling (parallel workers, model_copy)


@pytest.mark.parametrize(
    "duplicate",
    [
        lambda m: pickle.loads(pickle.dumps(m)),
        copy.deepcopy,
        copy.copy,
        lambda m: m.model_copy(deep=True),
        lambda m: m.model_copy(),
    ],
    ids=["pickle", "deepcopy", "copy", "model_copy-deep", "model_copy"],
)
def test_mutant_can_be_copied_and_pickled(duplicate) -> None:
    mutant = _mutant()

    again = duplicate(mutant)

    assert again == mutant
    assert again.details == mutant.details
    with pytest.raises(TypeError, match="read-only"):
        again.details["k"] = 1
    with pytest.raises(TypeError, match="read-only"):
        again.parameters["k"] = 1.0


def test_pickled_mutant_still_regenerates() -> None:
    again = pickle.loads(pickle.dumps(_mutant()))

    assert again.regenerate(RECORD) == again


def test_mappings_dump_as_plain_dicts() -> None:
    data = _mutant().model_dump()

    assert type(data["details"]) is dict
    assert type(data["parameters"]) is dict
    assert json.loads(_mutant().model_dump_json())["details"] == data["details"]


def test_nested_details_are_read_only() -> None:
    result = MutationResult(
        original_text=RECORD.answer,
        mutated_text=RECORD.answer + " Indeed.",
        changed=True,
        details={"swaps": [{"from": "Paris", "to": "Rome"}]},
    )
    mutant = Mutant.from_result(RECORD, VerbosityPaddingOperator(), result, intensity=0.5, seed=1)

    with pytest.raises(AttributeError):
        mutant.details["swaps"].append("x")
    with pytest.raises(TypeError, match="read-only"):
        mutant.details["swaps"][0]["to"] = "Lyon"
    assert mutant.model_dump()["details"] == {"swaps": [{"from": "Paris", "to": "Rome"}]}
    assert Mutant.model_validate_json(mutant.model_dump_json()) == mutant


def test_mutants_are_hashable() -> None:
    first, again = _mutant(), _mutant()
    other = Mutant.generate(RECORD, EntitySwapOperator(), intensity=1.0, seed=8)

    assert hash(first) == hash(again)
    assert len({first, again, other}) == len({first.mutant_id, other.mutant_id})


def test_model_copy_with_update_is_validated() -> None:
    with pytest.raises(ValidationError, match="mutant_id"):
        _mutant().model_copy(update={"seed": 5})
