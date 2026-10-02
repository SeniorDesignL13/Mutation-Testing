"""The ``mtlj`` command-line tool.

Run inside the stack with ``docker compose exec api mtlj --help``.
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
