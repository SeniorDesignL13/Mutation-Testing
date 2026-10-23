"""Mutant format: one operator's output for one record, with full provenance.

A mutant carries everything needed to reproduce and explain it: which
record it came from, which operator made it, with what intensity,
parameters and seed, and which way a good judge's score should move.
Running the same operator on the same record with the same settings
gives a byte-identical mutant, which ``Mutant.regenerate`` checks.

Reproducibility assumes the same environment. Operators that use spaCy
can find different entities under a different ``en_core_web_sm`` version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Self

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from mtlj.data.records import DatasetRecord
from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
)


@lru_cache
def _operators() -> dict[str, type[MutationOperator]]:
    """Map operator name to class. Imported here so spaCy isn't loaded at import time."""
    from mtlj.operators.degrading.entity_swap import EntitySwapOperator
    from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator

    return {op.metadata.name: op for op in (EntitySwapOperator, VerbosityPaddingOperator)}


def _check_inputs(intensity: float, parameters: Mapping[str, float]) -> None:
    """Reject inputs an operator can't honour, before it runs."""
    if not 0 <= intensity <= 1:
        raise ValueError(f"intensity must be in [0, 1], got {intensity}")
    # MutationOperator.apply() only takes intensity, so a parameter would be recorded but never
    # applied. Lift this once operators accept their ParameterSpec values.
    if parameters:
        raise ValueError(f"operators don't accept parameters yet, got {dict(parameters)}")


def make_mutant_id(
    record_id: str,
    original_text: str,
    operator_name: str,
    intensity: float,
    parameters: Mapping[str, float],
    seed: int,
) -> str:
    """Derive a mutant's id from its inputs, so the same inputs always give the same id.

    The original text is part of the id, so a record whose answer is later corrected gets new
    mutant ids. Numbers are normalised first, so intensity ``1`` and ``1.0`` give the same id,
    matching what the model stores after validation.
    """
    key = json.dumps(
        {
            "record_id": record_id,
            "original_sha256": _sha256(original_text),
            "operator_name": operator_name,
            "intensity": float(intensity),
            "parameters": {name: float(value) for name, value in parameters.items()},
            "seed": int(seed),
        },
        sort_keys=True,
    )
    return "mut_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _FrozenDict(dict):
    """A dict that refuses in-place edits but still pickles and deep-copies.

    MappingProxyType would also block edits, but it can't be pickled, so a mutant holding one
    couldn't be deep-copied or sent to a worker process.
    """

    def _readonly(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("mutant parameters and details are read-only")

    __setitem__ = __delitem__ = __ior__ = _readonly
    clear = pop = popitem = setdefault = update = _readonly

    def __reduce__(self) -> tuple:
        return (_FrozenDict, (dict(self),))


class Mutant(BaseModel):
    """One mutated answer and everything needed to reproduce it.

    Build one with ``Mutant.generate`` or ``Mutant.from_result`` rather than
    by hand: they take the expected direction and family from the operator,
    and fill in the id and hash.
    """

    mutant_id: str = Field(..., description="Derived from the inputs; see make_mutant_id.")
    record_id: str = Field(..., description="The DatasetRecord this mutant was made from.")
    operator_name: str = Field(..., description="Operator that made it, e.g. 'entity_swap'.")
    operator_family: OperatorFamily = Field(..., description="Degrading or preserving.")
    intensity: float = Field(..., ge=0, le=1, description="Intensity passed to the operator.")
    parameters: Mapping[str, float] = Field(
        default_factory=dict,
        description="Operator parameters, by ParameterSpec name. Empty until operators take them.",
    )
    seed: int = Field(..., description="Random seed passed to the operator. Always set.")
    expected_direction: ExpectedDirection = Field(
        ..., description="How a good judge's score should move, from the operator's metadata."
    )
    original_text: str = Field(..., description="The answer before mutation.")
    mutated_text: str = Field(..., description="The answer after mutation.")
    mutated_sha256: str = Field(..., description="SHA-256 of mutated_text as UTF-8.")
    changed: bool = Field(..., description="Whether the operator changed the text.")
    details: Mapping[str, Any] = Field(
        default_factory=dict,
        description="Operator-specific explanation, e.g. what was swapped for what.",
    )

    model_config = {"extra": "forbid", "frozen": True}

    @field_validator("parameters", "details", mode="after")
    @classmethod
    def _read_only(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        # frozen=True stops reassigning a field, not editing a dict in place, which would leave
        # mutant_id stale without any error.
        return _FrozenDict(value)

    @field_serializer("parameters", "details")
    def _as_dict(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return dict(value)

    @model_validator(mode="after")
    def _check_derived_fields(self) -> Self:
        _check_inputs(self.intensity, self.parameters)
        expected_id = make_mutant_id(
            self.record_id,
            self.original_text,
            self.operator_name,
            self.intensity,
            self.parameters,
            self.seed,
        )
        if self.mutant_id != expected_id:
            raise ValueError(f"mutant_id {self.mutant_id!r} doesn't match its inputs")
        if self.mutated_sha256 != _sha256(self.mutated_text):
            raise ValueError("mutated_sha256 doesn't match mutated_text")
        if self.changed != (self.mutated_text != self.original_text):
            raise ValueError(f"changed={self.changed} doesn't match the texts")
        return self

    @classmethod
    def from_result(
        cls,
        record: DatasetRecord,
        operator: MutationOperator,
        result: MutationResult,
        intensity: float,
        seed: int,
        parameters: Mapping[str, float] | None = None,
    ) -> Mutant:
        """Wrap an operator's result with the provenance needed to reproduce it."""
        if result.original_text != record.answer:
            raise ValueError(f"result was not made from record {record.record_id!r}'s answer")
        parameters = parameters or {}
        meta = operator.metadata
        return cls(
            mutant_id=make_mutant_id(
                record.record_id, result.original_text, meta.name, intensity, parameters, seed
            ),
            record_id=record.record_id,
            operator_name=meta.name,
            operator_family=meta.family,
            intensity=intensity,
            parameters=parameters,
            seed=seed,
            expected_direction=meta.expected_direction,
            original_text=result.original_text,
            mutated_text=result.mutated_text,
            mutated_sha256=_sha256(result.mutated_text),
            # From the texts, not result.changed: an operator can report no change while still
            # altering whitespace, and detection excludes "no change" mutants on this field.
            changed=result.mutated_text != result.original_text,
            details=result.details,
        )

    @classmethod
    def generate(
        cls,
        record: DatasetRecord,
        operator: MutationOperator,
        intensity: float,
        seed: int,
        parameters: Mapping[str, float] | None = None,
    ) -> Mutant:
        """Run ``operator`` on ``record``'s answer and return the resulting mutant."""
        _check_inputs(intensity, parameters or {})
        context = [p.text for p in record.context]
        result = operator.apply(record.answer, context, intensity=intensity, seed=seed)
        return cls.from_result(record, operator, result, intensity, seed, parameters)

    def regenerate(self, record: DatasetRecord) -> Mutant:
        """Rebuild this mutant from its provenance. Equal to ``self`` if reproducible."""
        if record.record_id != self.record_id:
            raise ValueError(f"mutant was made from {self.record_id!r}, not {record.record_id!r}")
        if record.answer != self.original_text:
            raise ValueError(f"record {record.record_id!r}'s answer has changed since mutation")
        try:
            operator_cls = _operators()[self.operator_name]
        except KeyError:
            raise ValueError(f"unknown operator {self.operator_name!r}") from None
        return Mutant.generate(record, operator_cls(), self.intensity, self.seed, self.parameters)
