"""Look up mutation operators by name.

Every concrete MutationOperator subclass in a family package under
``mtlj.operators`` (``mtlj.operators.degrading``, ``mtlj.operators.preserving``,
...) is found automatically, so adding an operator only means adding its module.
Discovery runs on first use rather than at import time, because importing the
operator modules loads spaCy.

    from mtlj.operators.registry import get_operator, available_operators

    available_operators()          # ["entity_swap", "verbosity_padding"]
    op = get_operator("entity_swap")
"""

import importlib
import inspect
import pkgutil
from typing import Any

from mtlj.operators.base import MutationOperator

DEFAULT_PACKAGE = "mtlj.operators"


class UnknownOperatorError(LookupError):
    """No operator is registered under the requested name."""

    def __init__(self, name: str, valid: list[str]) -> None:
        self.name = name
        self.valid = valid
        listed = ", ".join(valid) if valid else "none"
        super().__init__(f"unknown operator {name!r}; valid operators: {listed}")


class OperatorRegistry:
    """Maps operator names to MutationOperator classes.

    Args:
        package: the package whose family subpackages are searched for
            operators, or ``None`` to start empty and only hold classes
            passed to ``register()``.
    """

    def __init__(self, package: str | None = DEFAULT_PACKAGE) -> None:
        self._package = package
        self._operators: dict[str, type[MutationOperator]] = {}
        self._discovered = package is None

    def register(self, cls: type[MutationOperator]) -> type[MutationOperator]:
        """Add an operator class. Returns it, so this also works as a class decorator.

        Raises:
            TypeError: ``cls`` isn't a concrete MutationOperator subclass with metadata.
            ValueError: a different class is already registered under the same name.
        """
        if not (inspect.isclass(cls) and issubclass(cls, MutationOperator)):
            raise TypeError(f"{cls!r} is not a MutationOperator subclass")
        if inspect.isabstract(cls):
            raise TypeError(f"{cls.__qualname__} is abstract and can't be registered")
        metadata = getattr(cls, "metadata", None)
        if metadata is None:
            raise TypeError(f"{cls.__qualname__} has no metadata")

        name = metadata.name
        existing = self._operators.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"operator name {name!r} is used by both {_qualname(existing)} and {_qualname(cls)}"
            )
        self._operators[name] = cls
        return cls

    def get(self, name: str, **config: Any) -> MutationOperator:
        """A new instance of the operator named ``name``, built with ``config``.

        Raises:
            UnknownOperatorError: no operator has that name.
        """
        return self.get_class(name)(**config)

    def get_class(self, name: str) -> type[MutationOperator]:
        """The operator class named ``name``.

        Raises:
            UnknownOperatorError: no operator has that name.
        """
        self._discover()
        try:
            return self._operators[name]
        except KeyError:
            raise UnknownOperatorError(name, self.names()) from None

    def names(self) -> list[str]:
        """Names of every registered operator, sorted."""
        self._discover()
        return sorted(self._operators)

    def describe(self) -> list[dict]:
        """``describe()`` of every registered operator, sorted by name."""
        return [self._operators[name].describe() for name in self.names()]

    def __contains__(self, name: object) -> bool:
        self._discover()
        return name in self._operators

    def __len__(self) -> int:
        self._discover()
        return len(self._operators)

    def _discover(self) -> None:
        """Import every module of every family subpackage and register its operators."""
        if self._discovered:
            return
        # Set first: register() during discovery must not trigger discovery again.
        self._discovered = True
        try:
            self._import_families()
        except BaseException:
            # Try again next time rather than serving a half-filled registry.
            self._discovered = False
            raise

    def _import_families(self) -> None:
        root = importlib.import_module(self._package)
        for family in pkgutil.iter_modules(root.__path__, f"{root.__name__}."):
            if not family.ispkg:
                continue
            package = importlib.import_module(family.name)
            modules = [package]
            for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
                modules.append(importlib.import_module(info.name))
            for module in modules:
                for cls in vars(module).values():
                    if (
                        inspect.isclass(cls)
                        # Only classes defined here, not ones the module imported.
                        and cls.__module__ == module.__name__
                        and issubclass(cls, MutationOperator)
                        and not inspect.isabstract(cls)
                    ):
                        self.register(cls)


def _qualname(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


_default = OperatorRegistry()


def get_operator(name: str, **config: Any) -> MutationOperator:
    """A new instance of the built-in operator named ``name``."""
    return _default.get(name, **config)


def get_operator_class(name: str) -> type[MutationOperator]:
    """The built-in operator class named ``name``."""
    return _default.get_class(name)


def available_operators() -> list[str]:
    """Names of every built-in operator, sorted."""
    return _default.names()


def describe_operators() -> list[dict]:
    """``describe()`` of every built-in operator, sorted by name."""
    return _default.describe()


def register_operator(cls: type[MutationOperator]) -> type[MutationOperator]:
    """Add an operator to the default registry. Works as a class decorator."""
    return _default.register(cls)
