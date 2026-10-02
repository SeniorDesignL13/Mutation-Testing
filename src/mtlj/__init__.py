"""mtlj: Mutation Testing for LLM Judges."""

from importlib.metadata import version

# Read from pyproject.toml, so the version is only written in one place.
__version__ = version("mtlj")
