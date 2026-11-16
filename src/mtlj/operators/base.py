"""Base interface every mutation operator implements.

Declares what an operator IS before it runs: which family it belongs to
(degrading vs. preserving), what direction it guarantees the judge's score
should move, and what parameters control its intensity. The execution
engine reads this metadata to know what to expect from an operator's
output, without needing to know how the operator works internally.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class OperatorFamily(Enum):
    """Which family an operator belongs to."""

    DEGRADING = "degrading"
    PRESERVING = "preserving"


class ExpectedDirection(Enum):
    """The score-change direction an operator guarantees, by construction."""

    DECREASE = "decrease"
    UNCHANGED = "unchanged"


@dataclass
class ParameterSpec:
    """Describes one tunable parameter that controls an operator's intensity."""

    name: str
    description: str
    default: float
    min_value: float
    max_value: float


@dataclass
class OperatorMetadata:
    """Static metadata about an operator, independent of any specific input.

    ``version`` is a whole number from 1 up. Bump it with any change that can alter the
    operator's output for the same input, intensity and seed: it's part of every mutant's id,
    so mutants made by old and new code never share one (see docs/operators.md).
    """

    name: str
    family: OperatorFamily
    expected_direction: ExpectedDirection
    description: str
    version: int
    parameters: list[ParameterSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        # bool is an int subclass, so True would otherwise pass as version 1.
        if type(self.version) is not int or self.version < 1:
            raise ValueError(
                f"operator {self.name!r} version must be a whole number from 1, "
                f"got {self.version!r}"
            )


@dataclass
class MutationResult:
    """What an operator returns after mutating one record's answer text."""

    original_text: str
    mutated_text: str
    changed: bool
    details: dict


class MutationOperator(ABC):
    """Every mutation operator (degrading or preserving) implements this.

    Subclasses must set a class-level ``metadata`` attribute and implement
    ``apply()``.
    """

    metadata: OperatorMetadata

    @abstractmethod
    def apply(
        self,
        text: str,
        context: list[str],
        intensity: float = 1.0,
        seed: int | None = None,
    ) -> MutationResult:
        """Apply this operator to ``text`` (the answer being mutated).

        Args:
            text: the known-good text to mutate.
            context: the supporting passage(s) this text is grounded in.
            intensity: a value in [0, 1] controlling mutation strength.
                Interpretation is operator-specific.
            seed: random seed, so results are reproducible.

        Returns:
            A MutationResult describing the original text, mutated text,
            whether a change was made, and any extra details.
        """
        raise NotImplementedError

    @classmethod
    def describe(cls) -> dict:
        """Return a JSON-serializable summary of this operator's metadata."""
        m = cls.metadata
        return {
            "name": m.name,
            "family": m.family.value,
            "expected_direction": m.expected_direction.value,
            "description": m.description,
            "version": m.version,
            "parameters": [
                {
                    "name": p.name,
                    "description": p.description,
                    "default": p.default,
                    "min": p.min_value,
                    "max": p.max_value,
                }
                for p in m.parameters
            ],
        }
