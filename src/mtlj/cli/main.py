"""Entry point for the ``mtlj`` console script.

This is a shell: it wires up the CLI so the package installs and runs
correctly, but no mutation-testing commands are implemented yet.
"""

import typer

from mtlj import __version__

app = typer.Typer(
    name="mtlj",
    help="Mutation Testing for LLM Judges.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Mutation Testing for LLM Judges."""


@app.command()
def version() -> None:
    """Print the installed mtlj version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
