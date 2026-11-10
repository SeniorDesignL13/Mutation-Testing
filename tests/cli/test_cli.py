import subprocess
import sys
import threading
import tomllib
from collections import defaultdict
from pathlib import Path

import click
import pytest
from typer.testing import CliRunner

from mtlj import __version__
from mtlj.cli import main as cli
from mtlj.cli.main import JUDGES, app
from mtlj.engine.results import read_results
from mtlj.judges.callable import CallableJudge

RAGTRUTH_SAMPLE = Path(__file__).parent.parent / "data" / "ragtruth_sample.jsonl"

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


# --------------------------------------------------
# audit run and audit show
# --------------------------------------------------


def make_judge(output_base: float = 0.9) -> CallableJudge:
    """Gives every text the same score, with a little noise."""
    calls: defaultdict[str, int] = defaultdict(int)
    lock = threading.Lock()
    offsets = [0.0, 0.02, -0.02, 0.01, -0.01]

    def score(input, output, context):
        with lock:
            k = calls[output]
            calls[output] += 1
        return output_base + offsets[k % len(offsets)]

    return CallableJudge(score, name="stub", model="m", version="1")


@pytest.fixture
def stub_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(JUDGES, "stub", lambda model: make_judge())


def audit_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "audit",
        "run",
        "--dataset",
        str(RAGTRUTH_SAMPLE),
        "--judge",
        "stub",
        "--output",
        str(tmp_path / "run"),
        *extra,
    ]


def test_audit_run_prints_a_summary_and_saves_results(stub_judge, tmp_path: Path) -> None:
    result = runner.invoke(app, audit_args(tmp_path, "--samples", "3", "--seed", "4"))

    assert result.exit_code == 0, result.output
    assert "Planned:" in result.stdout
    assert "Status: complete" in result.stdout
    assert "Spurious sensitivity" in result.stdout
    saved = read_results(tmp_path / "run")
    assert (saved.run.samples, saved.run.base_seed) == (3, 4)
    assert saved.run.dataset == str(RAGTRUTH_SAMPLE)
    assert saved.run.n_records == 4
    assert saved.run.judge.name == "stub"


def test_audit_show_prints_the_same_summary_from_the_file(stub_judge, tmp_path: Path) -> None:
    run = runner.invoke(app, audit_args(tmp_path))

    shown = runner.invoke(app, ["audit", "show", str(tmp_path / "run")])

    assert shown.exit_code == 0
    assert shown.stdout.strip() in run.stdout


def test_audit_run_exits_1_when_the_judge_is_down(monkeypatch, tmp_path: Path) -> None:
    def down(input, output, context):
        # A permanent failure, so the runner doesn't wait out retries (see mtlj.engine.retry).
        raise PermissionError("invalid API key")

    monkeypatch.setitem(
        JUDGES, "stub", lambda model: CallableJudge(down, name="stub", model="m", version="1")
    )

    result = runner.invoke(app, audit_args(tmp_path))

    assert result.exit_code == 1
    assert "Status: aborted" in result.stdout
    assert "scorings failed" in result.stderr
    assert read_results(tmp_path / "run").run.status == "aborted"


def test_audit_run_warns_but_succeeds_when_only_some_scorings_fail(
    monkeypatch, tmp_path: Path
) -> None:
    good = make_judge()
    lock = threading.Lock()
    seen = [0]

    def flaky(input, output, context):
        with lock:
            seen[0] += 1
            fail = seen[0] % 7 == 0
        if fail:
            raise ValueError("score out of range")  # permanent, so not retried
        return good.score_raw(input, output, context)

    monkeypatch.setitem(
        JUDGES, "stub", lambda model: CallableJudge(flaky, name="stub", model="m", version="1")
    )

    result = runner.invoke(app, audit_args(tmp_path))

    assert result.exit_code == 0, result.output
    assert "scorings failed" in result.output


@pytest.mark.parametrize(
    ("extra", "option"),
    [
        (["--samples", "1"], "--samples"),
        (["--battery", "no_such_operator"], "--battery"),
        (["--judge", "no-such-judge"], "--judge"),
        (["--dataset", "no-such-file.jsonl"], "--dataset"),
        (["--limit", "0"], "--limit"),
        (["--concurrency", "0"], "--concurrency"),
    ],
)
def test_bad_arguments_exit_2_before_anything_runs(
    stub_judge, tmp_path: Path, extra: list[str], option: str
) -> None:
    result = runner.invoke(app, [*audit_args(tmp_path), *extra])

    assert result.exit_code == 2
    assert option in click.unstyle(result.output)
    assert not (tmp_path / "run").exists()


def test_audit_run_refuses_a_directory_in_use(stub_judge, tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "file").write_text("x", encoding="utf-8")

    result = runner.invoke(app, audit_args(tmp_path))

    assert result.exit_code == 2
    assert "isn't empty" in " ".join(result.output.split())


def test_battery_filter_runs_only_the_named_operators(stub_judge, tmp_path: Path) -> None:
    result = runner.invoke(app, audit_args(tmp_path, "--battery", "verbosity_padding"))

    assert result.exit_code == 0, result.output
    saved = read_results(tmp_path / "run")
    assert [o.operator for o in saved.operators] == ["verbosity_padding"]


def test_limit_uses_only_the_first_records(stub_judge, tmp_path: Path) -> None:
    runner.invoke(app, audit_args(tmp_path, "--limit", "1"))

    lines = (tmp_path / "run" / "originals.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 1
    assert read_results(tmp_path / "run").run.n_records == 1


def test_big_run_asks_first_and_stops_on_no(stub_judge, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "CONFIRM_ABOVE_SCORINGS", 1)

    result = runner.invoke(app, audit_args(tmp_path), input="n\n")

    assert result.exit_code == 1
    assert "Continue?" in result.output
    assert not (tmp_path / "run").exists()


def test_big_run_goes_ahead_with_yes(stub_judge, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "CONFIRM_ABOVE_SCORINGS", 1)

    result = runner.invoke(app, audit_args(tmp_path, "--yes"))

    assert result.exit_code == 0, result.output
    assert "Continue?" not in result.output


def test_gold_dataset_is_the_twenty_gold_records(stub_judge) -> None:
    records = cli._load_dataset("gold", None)

    assert len(records) == 20
    assert all(r.metadata["gold_set"] for r in records)


@pytest.mark.parametrize(
    ("name", "penalized"),
    [("deepeval-faithfulness", False), ("deepeval-faithfulness-penalized", True)],
)
def test_judge_names_build_the_deepeval_adapter(name: str, penalized: bool) -> None:
    pytest.importorskip("mtlj.judges.deepeval.faithfulness")

    judge = JUDGES[name]("some-model")

    assert judge.name == name
    assert judge.model == "some-model"
    assert judge.penalize_ambiguous_claims is penalized


def test_audit_show_exits_1_for_a_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", "show", str(tmp_path / "nothing")])

    assert result.exit_code == 1
    assert "Can't read results" in result.output


def test_audit_show_exits_1_for_an_unknown_version(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text('{"schema_version": 999}', encoding="utf-8")

    result = runner.invoke(app, ["audit", "show", str(tmp_path)])

    assert result.exit_code == 1
    assert "schema version" in result.output


def test_audit_show_exits_1_for_json_that_is_not_results(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text("[]", encoding="utf-8")

    result = runner.invoke(app, ["audit", "show", str(tmp_path)])

    assert result.exit_code == 1
    assert "Can't read results" in result.output


def test_commands_start_without_loading_deepeval_or_scipy() -> None:
    code = (
        "import sys; import mtlj.cli.main; "
        "sys.exit('deepeval' in sys.modules or 'scipy' in sys.modules)"
    )

    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_a_run_that_cannot_start_exits_2_without_a_traceback(monkeypatch, tmp_path: Path) -> None:
    unversioned = CallableJudge(lambda *_: 0.9, name="stub", model="m", version="")
    monkeypatch.setitem(JUDGES, "stub", lambda model: unversioned)

    result = runner.invoke(app, audit_args(tmp_path))

    assert result.exit_code == 2
    assert "the run can't start" in result.stderr
    assert "version" in result.stderr
    assert "Planned:" not in result.stdout
    assert not (tmp_path / "run").exists()


# --------------------------------------------------
# audit run --config
# --------------------------------------------------


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep each error on one line: typer's error box otherwise wraps long paths mid-message."""
    monkeypatch.setattr("typer.rich_utils.MAX_WIDTH", 1000)


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "audit.toml"
    path.write_text(text, encoding="utf-8")
    return path


def config_text(tmp_path: Path, extra: str = "") -> str:
    # Literal strings, so Windows paths need no escaping.
    return (
        f"dataset = '{RAGTRUTH_SAMPLE}'\njudge = \"stub\"\nsamples = 3\nseed = 4\n"
        f"output = '{tmp_path / 'run'}'\n{extra}"
    )


def test_audit_run_takes_its_settings_from_a_config_file(stub_judge, tmp_path: Path) -> None:
    config = write_config(tmp_path, config_text(tmp_path, 'battery = "verbosity_padding"\n'))

    result = runner.invoke(app, ["audit", "run", "--config", str(config)])

    assert result.exit_code == 0, result.output
    saved = read_results(tmp_path / "run")
    assert (saved.run.samples, saved.run.base_seed) == (3, 4)
    assert saved.run.n_records == 4
    assert [o.operator for o in saved.operators] == ["verbosity_padding"]


def test_options_override_the_config_file(stub_judge, tmp_path: Path) -> None:
    config = write_config(tmp_path, config_text(tmp_path))
    other = tmp_path / "other"

    result = runner.invoke(
        app,
        [
            "audit",
            "run",
            "--config",
            str(config),
            "--samples",
            "2",
            "--limit",
            "1",
            "--output",
            str(other),
        ],
    )

    assert result.exit_code == 0, result.output
    saved = read_results(other)
    assert (saved.run.samples, saved.run.base_seed, saved.run.n_records) == (2, 4, 1)
    assert not (tmp_path / "run").exists()


def test_the_run_saves_its_settings_and_they_run_again(stub_judge, tmp_path: Path) -> None:
    config = write_config(tmp_path, config_text(tmp_path))
    first = runner.invoke(app, ["audit", "run", "--config", str(config), "--limit", "2"])
    assert first.exit_code == 0, first.output

    saved = tmp_path / "run" / "config.toml"
    again = runner.invoke(
        app, ["audit", "run", "--config", str(saved), "--output", str(tmp_path / "again")]
    )

    assert again.exit_code == 0, again.output
    settings = tomllib.loads(saved.read_text("utf-8"))
    assert settings["limit"] == 2 and "output" not in settings
    a, b = read_results(tmp_path / "run"), read_results(tmp_path / "again")
    assert a.model_dump() == b.model_dump()


def test_a_run_from_options_alone_saves_its_settings_too(stub_judge, tmp_path: Path) -> None:
    result = runner.invoke(app, audit_args(tmp_path, "--samples", "2"))

    assert result.exit_code == 0, result.output
    settings = tomllib.loads((tmp_path / "run" / "config.toml").read_text("utf-8"))
    assert (settings["samples"], settings["judge"], settings["model"]) == (2, "stub", "m")
    assert settings["battery"] == "battery.toml"
    assert (tmp_path / "run" / "battery.toml").is_file()


def test_a_battery_file_can_be_given_as_an_option(stub_judge, tmp_path: Path) -> None:
    battery = tmp_path / "b.toml"
    battery.write_text('[[operators]]\nname = "entity_swap"\nintensities = [1.0]\n', "utf-8")

    result = runner.invoke(app, audit_args(tmp_path, "--battery", str(battery)))

    assert result.exit_code == 0, result.output
    assert [o.operator for o in read_results(tmp_path / "run").operators] == ["entity_swap"]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("samples = 1\n", "samples:"),
        ("colour = 'red'\n", "colour: unknown key"),
        ('battery = "gone.toml"\n', "battery file not found"),
        ('battery = "nope"\n', "unknown operators"),
        ('judge = "no-such-judge"\n', "unknown judge"),
    ],
)
def test_a_bad_config_exits_2_before_anything_runs(
    monkeypatch, wide, tmp_path: Path, extra: str, message: str
) -> None:
    def never(input, output, context):
        raise AssertionError("the judge was called")

    monkeypatch.setitem(
        JUDGES, "stub", lambda model: CallableJudge(never, name="stub", model="m", version="1")
    )
    # A later key replaces an earlier one only in Python, so drop the default line it changes.
    lines = [
        line
        for line in config_text(tmp_path).splitlines()
        if line.split(" = ")[0] != extra.split(" = ")[0]
    ]
    config = write_config(tmp_path, "\n".join(lines) + "\n" + extra)

    result = runner.invoke(app, ["audit", "run", "--config", str(config)])

    assert result.exit_code == 2
    output = " ".join(click.unstyle(result.output).split())
    assert message in output
    assert "audit.toml" in output
    assert "Traceback" not in output
    assert not (tmp_path / "run").exists()


def test_a_missing_config_file_exits_2(wide, tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", "run", "--config", str(tmp_path / "nope.toml")])

    assert result.exit_code == 2
    assert "audit file not found" in " ".join(result.output.split())


def test_dataset_is_required_without_a_config_file(wide) -> None:
    result = runner.invoke(app, ["audit", "run", "--judge", "stub"])

    assert result.exit_code == 2
    assert "required unless --config" in " ".join(click.unstyle(result.output).split())


def test_an_option_supplies_what_the_config_file_leaves_out(stub_judge, tmp_path: Path) -> None:
    config = write_config(tmp_path, 'judge = "stub"\nsamples = 1\n')

    result = runner.invoke(
        app,
        [
            "audit",
            "run",
            "--config",
            str(config),
            "--dataset",
            str(RAGTRUTH_SAMPLE),
            "--samples",
            "2",
            "--output",
            str(tmp_path / "run"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert read_results(tmp_path / "run").run.samples == 2


@pytest.mark.parametrize("option", ["--model", "--judge", "--battery", "--dataset"])
def test_an_empty_option_exits_2_without_a_traceback(
    stub_judge, wide, tmp_path: Path, option: str
) -> None:
    result = runner.invoke(app, [*audit_args(tmp_path), option, ""])

    assert result.exit_code == 2
    assert f"{option}: string should have at least 1 character" in click.unstyle(result.output)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert not (tmp_path / "run").exists()


def test_a_busy_default_directory_names_the_option_not_a_file(
    stub_judge, wide, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_RUNS_DIR", tmp_path)
    args = ["audit", "run", "--dataset", str(RAGTRUTH_SAMPLE), "--judge", "stub"]

    class Frozen(cli.datetime):
        @classmethod
        def now(cls, tz=None):
            return cli.datetime(2000, 1, 1)

    monkeypatch.setattr(cli, "datetime", Frozen)
    busy = tmp_path / "stub-20000101-000000"
    busy.mkdir()
    (busy / "file").write_text("x", encoding="utf-8")

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    output = click.unstyle(result.output)
    assert "Invalid value for --output" in output
    assert "None" not in output


def test_a_rerun_uses_the_saved_battery_after_the_original_changes(
    stub_judge, tmp_path: Path
) -> None:
    battery = tmp_path / "b.toml"
    battery.write_text('[[operators]]\nname = "entity_swap"\nintensities = [1.0]\n', "utf-8")
    config = write_config(tmp_path, config_text(tmp_path, 'battery = "b.toml"\n'))
    first = runner.invoke(app, ["audit", "run", "--config", str(config)])
    assert first.exit_code == 0, first.output

    battery.write_text('[[operators]]\nname = "verbosity_padding"\nintensities = [1.0]\n', "utf-8")
    again = runner.invoke(
        app,
        [
            "audit",
            "run",
            "--config",
            str(tmp_path / "run" / "config.toml"),
            "--output",
            str(tmp_path / "again"),
        ],
    )

    assert again.exit_code == 0, again.output
    assert [o.operator for o in read_results(tmp_path / "again").operators] == ["entity_swap"]


def test_a_config_file_that_isnt_utf8_exits_2(wide, tmp_path: Path) -> None:
    config = tmp_path / "audit.toml"
    config.write_bytes('dataset = "caf\xe9.jsonl"\n'.encode("latin-1"))

    result = runner.invoke(app, ["audit", "run", "--config", str(config)])

    assert result.exit_code == 2
    assert "the file isn't UTF-8" in click.unstyle(result.output)
