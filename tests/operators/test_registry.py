"""Tests for the operator registry."""

import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)
from mtlj.operators.degrading.entity_swap import EntitySwapOperator
from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator
from mtlj.operators.registry import (
    OperatorRegistry,
    UnknownOperatorError,
    available_operators,
    describe_operators,
    get_operator,
)


def _metadata(name: str) -> OperatorMetadata:
    return OperatorMetadata(
        name=name,
        family=OperatorFamily.PRESERVING,
        expected_direction=ExpectedDirection.UNCHANGED,
        description="test operator",
    )


class NoOp(MutationOperator):
    metadata = _metadata("noop")

    def __init__(self, level: int = 0) -> None:
        self.level = level

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        return MutationResult(text, text, False, {})


class AlsoNoOp(NoOp):
    """A different class that reuses NoOp's name."""


class Abstract(MutationOperator):
    """Doesn't implement apply()."""

    metadata = _metadata("abstract")


OPERATOR_SOURCE = """
from mtlj.operators.base import (
    ExpectedDirection, MutationOperator, MutationResult, OperatorFamily, OperatorMetadata,
)


class {cls}(MutationOperator):
    metadata = OperatorMetadata(
        name="{name}",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description="fake",
    )

    def apply(self, text, context, intensity=1.0, seed=None):
        return MutationResult(text, text, False, {{}})
"""


@pytest.fixture
def make_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a throwaway operator package and return its import name.

    ``files`` maps a path inside the package to its source. Every directory gets an
    ``__init__.py``. Each package has a unique name so imports never collide.
    """
    monkeypatch.syspath_prepend(str(tmp_path))
    created: list[str] = []

    def make(files: dict[str, str]) -> str:
        name = f"fakeops_{uuid.uuid4().hex}"
        root = tmp_path / name
        for rel, source in {"__init__.py": "", **files}.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source))
        for directory in [root, *root.rglob("*")]:
            if directory.is_dir() and not (directory / "__init__.py").exists():
                (directory / "__init__.py").write_text("")
        created.append(name)
        return name

    yield make
    for module in list(sys.modules):
        if any(module == n or module.startswith(f"{n}.") for n in created):
            del sys.modules[module]


# -- discovery of the real operators ------------------------------------------------


def test_discovers_built_in_operators() -> None:
    registry = OperatorRegistry()
    assert {"entity_swap", "verbosity_padding"} <= set(registry.names())
    assert registry.get_class("entity_swap") is EntitySwapOperator
    assert registry.get_class("verbosity_padding") is VerbosityPaddingOperator


def test_names_are_sorted() -> None:
    names = OperatorRegistry().names()
    assert names == sorted(names)


def test_describe_matches_each_operator() -> None:
    described = OperatorRegistry().describe()
    by_name = {d["name"]: d for d in described}
    assert by_name["entity_swap"] == EntitySwapOperator.describe()
    assert by_name["verbosity_padding"] == VerbosityPaddingOperator.describe()
    assert [d["name"] for d in described] == OperatorRegistry().names()


def test_module_helpers_use_built_in_operators() -> None:
    assert "entity_swap" in available_operators()
    assert isinstance(get_operator("verbosity_padding"), VerbosityPaddingOperator)
    assert {d["name"] for d in describe_operators()} == set(available_operators())


# -- discovery of a package on disk -------------------------------------------------


def test_discovers_operators_in_every_family(make_package) -> None:
    package = make_package(
        {
            "degrading/one.py": OPERATOR_SOURCE.format(cls="One", name="one"),
            "preserving/two.py": OPERATOR_SOURCE.format(cls="Two", name="two"),
            "preserving/nested/three.py": OPERATOR_SOURCE.format(cls="Three", name="three"),
        }
    )
    assert OperatorRegistry(package).names() == ["one", "three", "two"]


def test_discovery_skips_imported_and_abstract_classes(make_package) -> None:
    package = make_package(
        {
            "degrading/one.py": OPERATOR_SOURCE.format(cls="One", name="one"),
            # Re-imports One and defines an abstract base; neither should register twice
            # or at all.
            "degrading/two.py": """
                from abc import abstractmethod

                from mtlj.operators.base import MutationOperator

                from .one import One


                class Base(MutationOperator):
                    @abstractmethod
                    def extra(self): ...
            """,
        }
    )
    registry = OperatorRegistry(package)
    assert registry.names() == ["one"]


def test_modules_outside_families_are_ignored(make_package) -> None:
    package = make_package(
        {
            "loose.py": OPERATOR_SOURCE.format(cls="Loose", name="loose"),
            "degrading/one.py": OPERATOR_SOURCE.format(cls="One", name="one"),
        }
    )
    assert OperatorRegistry(package).names() == ["one"]


def test_discovery_rejects_duplicate_names(make_package) -> None:
    package = make_package(
        {
            "degrading/one.py": OPERATOR_SOURCE.format(cls="One", name="same"),
            "preserving/two.py": OPERATOR_SOURCE.format(cls="Two", name="same"),
        }
    )
    with pytest.raises(ValueError, match="'same'"):
        OperatorRegistry(package).names()


def test_discovery_is_lazy(make_package) -> None:
    package = make_package({"degrading/one.py": OPERATOR_SOURCE.format(cls="One", name="one")})
    OperatorRegistry(package)
    assert f"{package}.degrading.one" not in sys.modules


# -- register / get -----------------------------------------------------------------


def test_get_returns_a_new_instance_each_call() -> None:
    registry = OperatorRegistry(None)
    registry.register(NoOp)
    first, second = registry.get("noop"), registry.get("noop")
    assert isinstance(first, NoOp)
    assert first is not second


def test_get_passes_config_to_the_constructor() -> None:
    registry = OperatorRegistry(None)
    registry.register(NoOp)
    assert registry.get("noop", level=3).level == 3
    assert registry.get("noop").level == 0


def test_unknown_name_lists_valid_names() -> None:
    registry = OperatorRegistry(None)
    registry.register(NoOp)
    with pytest.raises(UnknownOperatorError) as excinfo:
        registry.get("nope")
    assert isinstance(excinfo.value, LookupError)
    assert excinfo.value.valid == ["noop"]
    assert "'nope'" in str(excinfo.value)
    assert "noop" in str(excinfo.value)


def test_unknown_name_in_default_registry_lists_built_ins() -> None:
    with pytest.raises(UnknownOperatorError, match="entity_swap"):
        get_operator("nope")


def test_register_works_as_a_decorator() -> None:
    registry = OperatorRegistry(None)
    assert registry.register(NoOp) is NoOp
    assert "noop" in registry
    assert len(registry) == 1


def test_registering_the_same_class_twice_is_harmless() -> None:
    registry = OperatorRegistry(None)
    registry.register(NoOp)
    registry.register(NoOp)
    assert registry.names() == ["noop"]


def test_two_classes_cannot_share_a_name() -> None:
    registry = OperatorRegistry(None)
    registry.register(NoOp)
    with pytest.raises(ValueError, match=r"NoOp.*AlsoNoOp"):
        registry.register(AlsoNoOp)
    assert registry.get_class("noop") is NoOp


def test_abstract_classes_cannot_be_registered() -> None:
    registry = OperatorRegistry(None)
    with pytest.raises(TypeError, match="abstract"):
        registry.register(Abstract)
    with pytest.raises(TypeError, match="abstract"):
        registry.register(MutationOperator)


def test_non_operators_cannot_be_registered() -> None:
    registry = OperatorRegistry(None)
    with pytest.raises(TypeError):
        registry.register(dict)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        registry.register(NoOp())  # type: ignore[arg-type]


def test_operator_without_metadata_cannot_be_registered() -> None:
    class Nameless(MutationOperator):
        def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
            return MutationResult(text, text, False, {})

    with pytest.raises(TypeError, match="metadata"):
        OperatorRegistry(None).register(Nameless)
