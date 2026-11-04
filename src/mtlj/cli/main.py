"""The ``mtlj`` command-line tool.

Try it with ``uv run mtlj --help`` (or ``docker compose exec api mtlj --help``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from mtlj import __version__
from mtlj.data.ragtruth import load_corpus_v1, load_ragtruth
from mtlj.data.records import DatasetRecord
from mtlj.engine.generator import BatteryItem, default_battery, generate_mutants
from mtlj.engine.runner import DEFAULT_MAX_CONCURRENCY, DEFAULT_SAMPLES
from mtlj.judges.base import Judge

app = typer.Typer(
    name="mtlj",
    help="Mutation Testing for LLM Judges.",
    no_args_is_help=True,
)
audit_app = typer.Typer(help="Run audits and read their results.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")

CORPUS_V1 = "corpus-v1"
GOLD_SET = "gold"
DEFAULT_BATTERY = "default"
DEFAULT_RUNS_DIR = Path("runs")
# Runs bigger than this ask first: judges bill per call, and a whole corpus takes hours.
CONFIRM_ABOVE_SCORINGS = 1000


def _deepeval_judge(model: str | None, *, penalized: bool) -> Judge:
    # Imported here so every other command starts without loading the adapter.
    from mtlj.judges.deepeval.faithfulness import DEFAULT_MODEL, DeepEvalFaithfulnessJudge

    return DeepEvalFaithfulnessJudge(model or DEFAULT_MODEL, penalize_ambiguous_claims=penalized)


# Judge name -> what builds it from --model.
JUDGES: dict[str, Callable[[str | None], Judge]] = {
    "deepeval-faithfulness": lambda model: _deepeval_judge(model, penalized=False),
    "deepeval-faithfulness-penalized": lambda model: _deepeval_judge(model, penalized=True),
}


def _load_dataset(dataset: str, limit: int | None) -> list[DatasetRecord]:
    """The records ``dataset`` names: the frozen corpus, its gold set, or a RAGTruth file."""
    if dataset in (CORPUS_V1, GOLD_SET):
        records = load_corpus_v1().records
        if dataset == GOLD_SET:
            records = [r for r in records if r.metadata["gold_set"]]
    else:
        path = Path(dataset)
        if not path.is_file():
            raise typer.BadParameter(
                f"{dataset!r} is not {CORPUS_V1!r}, {GOLD_SET!r} or a file", param_hint="--dataset"
            )
        loaded = load_ragtruth(path)
        for skipped in loaded.skipped:
            typer.echo(f"Skipped line {skipped.line_number}: {skipped.reason}", err=True)
        records = loaded.records
    if not records:
        raise typer.BadParameter(f"{dataset!r} has no records", param_hint="--dataset")
    return records[:limit]


def _select_battery(battery: str) -> list[BatteryItem]:
    """The default battery, or the comma-separated operators of it that ``battery`` names."""
    items = default_battery()
    if battery == DEFAULT_BATTERY:
        return items
    by_name = {item.operator.metadata.name: item for item in items}
    names = [name.strip() for name in battery.split(",")]
    unknown = sorted(set(names) - by_name.keys())
    if unknown:
        raise typer.BadParameter(
            f"unknown operators {unknown}; choose from {sorted(by_name)}", param_hint="--battery"
        )
    return [by_name[name] for name in dict.fromkeys(names)]


def _select_judge(name: str, model: str | None) -> Judge:
    if name not in JUDGES:
        raise typer.BadParameter(
            f"unknown judge {name!r}; choose from {sorted(JUDGES)}", param_hint="--judge"
        )
    return JUDGES[name](model)


@app.callback()
def main() -> None:
    """Mutation Testing for LLM Judges."""


@app.command()
def version() -> None:
    """Print the installed mtlj version."""
    typer.echo(__version__)


@audit_app.command("run")
def audit_run(
    dataset: Annotated[
        str,
        typer.Option(help=f"'{CORPUS_V1}', '{GOLD_SET}', or the path of a RAGTruth .jsonl file."),
    ],
    judge: Annotated[str, typer.Option(help="The judge to audit.")] = "deepeval-faithfulness",
    model: Annotated[str | None, typer.Option(help="Model the judge uses.")] = None,
    battery: Annotated[
        str, typer.Option(help="'default', or comma-separated operators from it.")
    ] = DEFAULT_BATTERY,
    samples: Annotated[
        int, typer.Option(min=2, help="Scorings per text. Noise needs at least 2.")
    ] = DEFAULT_SAMPLES,
    seed: Annotated[int, typer.Option(help="Seed for generating mutants.")] = 0,
    limit: Annotated[int | None, typer.Option(min=1, help="Use only the first N records.")] = None,
    concurrency: Annotated[
        int, typer.Option(min=1, help="Judge calls in flight at once.")
    ] = DEFAULT_MAX_CONCURRENCY,
    output: Annotated[
        Path | None, typer.Option(help="Empty directory for the run. Default: under runs/.")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Don't ask before a big run.")] = False,
) -> None:
    """Generate mutants, score them with a judge, and report how the judge did.

    Exits 0 when the run completes, even if some scorings failed, and 1 when it stops early or
    can't measure the judge's noise.
    """
    # Imported here because they load scipy, which no other command should wait for.
    from mtlj.engine.audit import check_run_dir, count_scorings, run_audit
    from mtlj.engine.results import COMPLETE, format_summary

    chosen = _select_judge(judge, model)
    run_dir = output or DEFAULT_RUNS_DIR / f"{chosen.name}-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        check_run_dir(run_dir)
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc), param_hint="--output") from exc
    records = _load_dataset(dataset, limit)
    items = _select_battery(battery)

    generation = generate_mutants(records, items, base_seed=seed)
    scorings = count_scorings(generation, samples)
    typer.echo(
        f"Planned: {len(generation.originals)} originals and {len(generation.mutants)} mutants, "
        f"{samples} scorings each: {scorings} scorings with {chosen.name} ({chosen.model})."
    )
    if scorings > CONFIRM_ABOVE_SCORINGS and not yes:
        typer.confirm("Judges bill per call. Continue?", abort=True)

    results = run_audit(
        generation,
        chosen,
        run_dir,
        dataset=dataset,
        samples=samples,
        max_concurrency=concurrency,
    )

    typer.echo(f"\n{format_summary(results)}\n\nSaved to {run_dir}")
    if results.run.status != COMPLETE:
        raise typer.Exit(code=1)
    if results.run.scorings.errors:
        typer.echo(f"Warning: {results.run.scorings.errors} scorings failed.", err=True)


@audit_app.command("show")
def audit_show(
    path: Annotated[Path, typer.Argument(help="A run's directory, or its results.json.")],
) -> None:
    """Print a finished run's results, from its results file alone."""
    from mtlj.engine.results import format_summary, read_results

    try:
        results = read_results(path)
    except (OSError, ValueError) as exc:
        typer.echo(f"Can't read results: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(format_summary(results))
