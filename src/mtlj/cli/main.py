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
from mtlj.engine.audit_config import (
    CORPUS_V1,
    DEFAULT_BATTERY,
    DEFAULT_JUDGE,
    GOLD_SET,
    AuditConfig,
    AuditConfigError,
    build_audit_config,
    read_audit_file,
    select_battery,
)
from mtlj.engine.generator import BatteryItem, generate_mutants
from mtlj.engine.runner import DEFAULT_MAX_CONCURRENCY, DEFAULT_SAMPLES
from mtlj.judges.base import Judge

app = typer.Typer(
    name="mtlj",
    help="Mutation Testing for LLM Judges.",
    no_args_is_help=True,
)
audit_app = typer.Typer(help="Run audits and read their results.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")

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


def _load_dataset(
    dataset: str, limit: int | None, param_hint: str = "--dataset"
) -> list[DatasetRecord]:
    """The records ``dataset`` names: the frozen corpus, its gold set, or a RAGTruth file."""
    if dataset in (CORPUS_V1, GOLD_SET):
        records = load_corpus_v1().records
        if dataset == GOLD_SET:
            records = [r for r in records if r.metadata["gold_set"]]
    else:
        path = Path(dataset)
        if not path.is_file():
            raise typer.BadParameter(
                f"{dataset!r} is not {CORPUS_V1!r}, {GOLD_SET!r} or a file", param_hint=param_hint
            )
        loaded = load_ragtruth(path)
        for skipped in loaded.skipped:
            typer.echo(f"Skipped line {skipped.line_number}: {skipped.reason}", err=True)
        records = loaded.records
    if not records:
        raise typer.BadParameter(f"{dataset!r} has no records", param_hint=param_hint)
    return records[:limit]


def _select_battery(battery: str, param_hint: str = "--battery") -> list[BatteryItem]:
    """The battery ``battery`` names (see ``mtlj.engine.audit_config.select_battery``)."""
    try:
        return select_battery(battery)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint=param_hint) from exc


def _select_judge(name: str, model: str | None, param_hint: str = "--judge") -> Judge:
    if name not in JUDGES:
        raise typer.BadParameter(
            f"unknown judge {name!r}; choose from {sorted(JUDGES)}", param_hint=param_hint
        )
    return JUDGES[name](model)


def _settings(
    config: Path | None, flags: dict[str, object]
) -> tuple[AuditConfig, Callable[[str], str]]:
    """The run's settings: the audit file's, with every option given on the command line on top.

    The two are merged before anything is checked, so an option can supply or fix a value the
    file lacks or gets wrong. Returns the settings with a function giving, for a setting, where
    a bad value came from: the file's key if the file set it, otherwise the option, which is
    also where a default is changed.
    """
    given = {key: value for key, value in flags.items() if value is not None}
    file_values: dict[str, object] = {}
    try:
        if config is not None:
            file_values = read_audit_file(config)
        settings = build_audit_config(file_values, given, source=config)
    except AuditConfigError as exc:
        # The message says where the bad value came from, so it needs no param_hint.
        raise typer.BadParameter(str(exc)) from exc

    def where(key: str) -> str:
        if key in file_values and key not in given:
            return f"'{key}' in {config}"
        return f"--{key}"

    return settings, where


@app.callback()
def main() -> None:
    """Mutation Testing for LLM Judges."""


@app.command()
def version() -> None:
    """Print the installed mtlj version."""
    typer.echo(__version__)


@audit_app.command("run")
def audit_run(
    config: Annotated[
        Path | None,
        typer.Option(
            help="An audit file (TOML) with the settings below. Options given here override it."
        ),
    ] = None,
    dataset: Annotated[
        str | None,
        typer.Option(
            help=f"'{CORPUS_V1}', '{GOLD_SET}', or the path of a RAGTruth .jsonl file. "
            "Required unless --config gives one."
        ),
    ] = None,
    judge: Annotated[
        str | None, typer.Option(help=f"The judge to audit. Default: {DEFAULT_JUDGE}.")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model the judge uses.")] = None,
    battery: Annotated[
        str | None,
        typer.Option(
            help=f"'{DEFAULT_BATTERY}', comma-separated operators from it, or a battery .toml "
            f"file. Default: {DEFAULT_BATTERY}."
        ),
    ] = None,
    samples: Annotated[
        int | None,
        typer.Option(
            min=2, help=f"Scorings per text. Noise needs at least 2. Default: {DEFAULT_SAMPLES}."
        ),
    ] = None,
    seed: Annotated[
        int | None, typer.Option(help="Seed for generating mutants. Default: 0.")
    ] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Use only the first N records.")] = None,
    concurrency: Annotated[
        int | None,
        typer.Option(
            min=1, help=f"Judge calls in flight at once. Default: {DEFAULT_MAX_CONCURRENCY}."
        ),
    ] = None,
    output: Annotated[
        Path | None, typer.Option(help="Empty directory for the run. Default: under runs/.")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Don't ask before a big run.")] = False,
) -> None:
    """Generate mutants, score them with a judge, and report how the judge did.

    Settings come from the options, an audit file (--config), or both. All of them are checked
    before any mutant is generated or any judge is called. The run's directory gets the
    settings it used, as config.toml and battery.toml, which --config can rerun.

    Exits 0 when the run completes, even if some scorings failed, and 1 when it stops early or
    can't measure the judge's noise.
    """
    # Imported here because they load scipy, which no other command should wait for.
    from mtlj.engine.audit import check_can_start, check_run_dir, run_audit
    from mtlj.engine.results import COMPLETE, format_summary

    settings, hint = _settings(
        config,
        {
            "dataset": dataset,
            "judge": judge,
            "model": model,
            "battery": battery,
            "samples": samples,
            "seed": seed,
            "limit": limit,
            "concurrency": concurrency,
            "output": None if output is None else str(output),
        },
    )
    chosen = _select_judge(settings.judge, settings.model, hint("judge"))
    run_dir = (
        Path(settings.output)
        if settings.output is not None
        else DEFAULT_RUNS_DIR / f"{chosen.name}-{datetime.now():%Y%m%d-%H%M%S}"
    )
    try:
        check_run_dir(run_dir)
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc), param_hint=hint("output")) from exc
    records = _load_dataset(settings.dataset, settings.limit, hint("dataset"))
    items = _select_battery(settings.battery, hint("battery"))
    # The battery as loaded and the judge's actual model, so a rerun matches this run.
    saved = settings.saved_files(items, chosen.model)
    samples, concurrency = settings.samples, settings.concurrency

    generation = generate_mutants(records, items, base_seed=settings.seed)
    try:
        plan = check_can_start(
            generation, chosen, run_dir, samples=samples, max_concurrency=concurrency
        )
    except FileExistsError as exc:
        raise typer.BadParameter(str(exc), param_hint=hint("output")) from exc
    except ValueError as exc:
        # Nothing has been written or paid for, so this is a usage error, not a crash.
        typer.echo(f"Error: the run can't start: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    scorings = len(plan)
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
        dataset=settings.dataset,
        samples=samples,
        max_concurrency=concurrency,
        saved_files=saved,
    )

    typer.echo(f"\n{format_summary(results)}\n\nSaved to {run_dir}")
    # Before the exit: when a run doesn't complete, failed scorings are usually why.
    if results.run.scorings.errors:
        typer.echo(f"Warning: {results.run.scorings.errors} scorings failed.", err=True)
    if results.run.status != COMPLETE:
        raise typer.Exit(code=1)


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
