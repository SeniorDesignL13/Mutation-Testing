"""The ``mtlj`` command-line tool.

Try it with ``uv run mtlj --help`` (or ``docker compose exec api mtlj --help``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import click
import typer

from mtlj import __version__
from mtlj.data.ragtruth import load_corpus_v1, load_ragtruth
from mtlj.data.records import DatasetRecord
from mtlj.engine.audit_config import (
    CORPUS_V1,
    DEFAULT_BATTERY,
    DEFAULT_JUDGE,
    GOLD_SET,
    SAVED_CONFIG_FILE,
    AuditConfig,
    AuditConfigError,
    build_audit_config,
    load_audit_config,
    option_name,
    read_audit_file,
    select_battery,
)
from mtlj.engine.generator import BatteryItem, generate_mutants
from mtlj.engine.runner import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_SAMPLES,
    NO_RATE_LIMIT,
    RunLimits,
    RunSummary,
    describe_limits,
    resolve_limits,
)
from mtlj.judges.base import Judge

if TYPE_CHECKING:
    from mtlj.engine.audit import RetryErrors
    from mtlj.engine.results import AuditResults
    from mtlj.engine.run_state import AuditState

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
        return option_name(key)

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
            min=1,
            help="Judge calls in flight at once. Default: the judge's own, "
            f"or {DEFAULT_MAX_CONCURRENCY}.",
        ),
    ] = None,
    rate_limit: Annotated[
        float | None,
        typer.Option(
            min=0,
            help="Most judge calls started a minute, retries included; 0 for no limit. "
            "Default: the judge's own (120 for DeepEval), or none.",
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
    from mtlj.engine.run_state import RunInUseError

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
            "rate_limit": rate_limit,
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
    try:
        limits = resolve_limits(chosen, settings.concurrency, settings.rate_limit)
    except (TypeError, ValueError) as exc:
        # The options were checked already, so this is a judge with a bad default.
        typer.echo(f"Error: the run can't start: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    # The battery as loaded, the judge's actual model and limits, so a rerun matches this run.
    saved = settings.saved_files(items, chosen.model, limits)
    samples = settings.samples
    # As resolved, so the judge's defaults aren't looked up again (0 keeps "no limit").
    concurrency, rate = limits.max_concurrency, limits.rate_limit or NO_RATE_LIMIT

    generation = generate_mutants(records, items, base_seed=settings.seed)
    try:
        plan = check_can_start(
            generation,
            chosen,
            run_dir,
            samples=samples,
            max_concurrency=concurrency,
            rate_limit=rate,
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
        f"{samples} scorings each: {scorings} scorings with {chosen.name} ({chosen.model}), "
        f"{describe_limits(limits)}."
    )
    if scorings > CONFIRM_ABOVE_SCORINGS and not yes:
        typer.confirm("Judges bill per call. Continue?", abort=True)

    summaries: list[RunSummary] = []
    try:
        results = run_audit(
            generation,
            chosen,
            run_dir,
            dataset=settings.dataset,
            samples=samples,
            max_concurrency=concurrency,
            rate_limit=rate,
            saved_files=saved,
            on_summary=summaries.append,
        )
    except FileExistsError as exc:
        # Another run took the directory after the checks above; nothing was written to it.
        raise typer.BadParameter(str(exc), param_hint=hint("output")) from exc
    except RunInUseError as exc:
        typer.echo(f"Error: the run can't start: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _report(results, summaries, run_dir)


def _report(results: AuditResults, summaries: list[RunSummary], run_dir: Path) -> None:
    """Print a finished run's summary, and exit 1 unless it completed."""
    from mtlj.engine.results import ABORTED, COMPLETE, format_summary

    typer.echo(f"\n{format_summary(results)}")
    for summary in summaries:
        if summary.rate_limit is not None:
            typer.echo(
                f"Rate limit: {summary.rate_limited} calls waited "
                f"{summary.rate_limit_wait_s:.1f} s for their turn in all"
            )
    typer.echo(f"\nSaved to {run_dir}")
    # Before the exit: when a run doesn't complete, failed scorings are usually why.
    if results.run.scorings.errors:
        typer.echo(f"Warning: {results.run.scorings.errors} scorings failed.", err=True)
    if results.run.status == ABORTED:
        typer.echo(f"Resume it with: mtlj audit resume {run_dir}", err=True)
    if results.run.status != COMPLETE:
        raise typer.Exit(code=1)


def _legacy_state(
    run_dir: Path, settings: AuditConfig, judge: Judge, limits: RunLimits
) -> AuditState:
    """What a run made before runs kept an audit.json would have put in one.

    Its ``config.toml`` has the samples and limits. The dataset label is the one its
    ``results.json`` was built with, if it has one; ``config.toml`` has it as an absolute path.
    Nothing records the judge's class or settings, so only its name, model and version, which
    every stored row holds, can be checked.
    """
    from mtlj.engine.audit import EVALUATIONS_FILE
    from mtlj.engine.results import RESULTS_FILE, read_results
    from mtlj.engine.run_state import AuditState, JudgeFingerprint, NotResumableError

    if not (run_dir / EVALUATIONS_FILE).exists() and not (run_dir / RESULTS_FILE).exists():
        raise NotResumableError(
            f"{run_dir} has no audit.json: its setup never finished, so nothing was scored. "
            "Start a new run instead."
        )
    dataset = settings.dataset
    with contextlib.suppress(OSError, ValueError):
        dataset = read_results(run_dir).run.dataset
    state = AuditState.for_run(judge, dataset=dataset, samples=settings.samples, limits=limits)
    return state.model_copy(update={"judge": JudgeFingerprint.unverified(judge)})


@audit_app.command("resume")
def audit_resume(
    run_dir: Annotated[Path, typer.Argument(help="The directory of the run to resume.")],
    concurrency: Annotated[
        int | None,
        typer.Option(min=1, help="Judge calls in flight at once. Default: the run's own."),
    ] = None,
    rate_limit: Annotated[
        float | None,
        typer.Option(
            min=0,
            help="Most judge calls started a minute, retries included; 0 for no limit. "
            "Default: the run's own.",
        ),
    ] = None,
    retry_errors: Annotated[
        str,
        typer.Option(
            help="Which failed scorings to score again: 'transient' (timeouts, rate limits, "
            "the judge being down), 'all', or 'none'.",
        ),
    ] = "transient",
    yes: Annotated[bool, typer.Option("--yes", help="Don't ask before a big run.")] = False,
) -> None:
    """Carry on a run that stopped, scoring only what it's missing.

    The run is resumed from its own directory: the same mutants, judge, model and samples, so
    its results are the ones an uninterrupted run would have given. Everything is checked
    before any judge is called.

    Exits 0 when the run completes, 1 when it stops early again, and 2 when it can't be resumed.
    """
    from mtlj.engine.audit import (
        RETRY_ERRORS_MODES,
        execute_resume,
        prepare_resume,
        resume_limits,
    )
    from mtlj.engine.run_state import (
        NotResumableError,
        RunInUseError,
        lock_run,
        read_audit_state,
    )

    if retry_errors not in RETRY_ERRORS_MODES:
        raise typer.BadParameter(
            f"{retry_errors!r} isn't one of {', '.join(RETRY_ERRORS_MODES)}",
            param_hint="--retry-errors",
        )
    mode = cast("RetryErrors", retry_errors)

    def cannot(reason: object) -> typer.Exit:
        typer.echo(f"Error: the run can't be resumed: {reason}", err=True)
        return typer.Exit(code=2)

    config_path = run_dir / SAVED_CONFIG_FILE
    if not run_dir.is_dir():
        raise cannot(f"{run_dir} isn't a run directory")
    if not config_path.is_file():
        raise cannot(
            f"{run_dir} has no {SAVED_CONFIG_FILE}, so its judge isn't known. Runs started "
            "from Python can be resumed with mtlj.engine.audit.resume_audit."
        )
    try:
        settings = load_audit_config(config_path)
        chosen = _select_judge(settings.judge, settings.model, f"'judge' in {config_path}")
    except (AuditConfigError, typer.BadParameter) as exc:
        message = exc.format_message() if isinstance(exc, click.ClickException) else exc
        raise cannot(message) from exc

    summaries: list[RunSummary] = []
    try:
        # Held from the check to the end, so a run still scoring is refused before anything is
        # shown or asked, and what's shown is what's resumed.
        with lock_run(run_dir):
            state = read_audit_state(run_dir)
            if state is None:
                saved = resolve_limits(chosen, settings.concurrency, settings.rate_limit)
                state = _legacy_state(run_dir, settings, chosen, saved)
                typer.echo(
                    f"Warning: {run_dir} was made before runs kept an audit.json; resuming from "
                    f"its {SAVED_CONFIG_FILE}. Only the judge's name, model and version can be "
                    "checked against the run's.",
                    err=True,
                )
            plan = prepare_resume(run_dir, chosen, retry_errors=mode, state=state)
            limits = resume_limits(plan, chosen, concurrency, rate_limit)

            failed = f", {len(plan.retried)} failed ones scored again" if plan.retried else ""
            typer.echo(
                f"Resuming {run_dir}: {len(plan.kept)} of {plan.total} scorings stored, "
                f"{plan.remaining} to score{failed}, with {chosen.name} ({chosen.model}), "
                f"{describe_limits(limits)}."
            )
            if plan.remaining > CONFIRM_ABOVE_SCORINGS and not yes:
                typer.confirm("Judges bill per call. Continue?", abort=True)
            results = execute_resume(
                plan,
                chosen,
                max_concurrency=limits.max_concurrency,
                rate_limit=limits.rate_limit or NO_RATE_LIMIT,
                on_summary=summaries.append,
            )
    except (NotResumableError, RunInUseError, TypeError, ValueError) as exc:
        raise cannot(exc) from exc
    _report(results, summaries, run_dir)


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
