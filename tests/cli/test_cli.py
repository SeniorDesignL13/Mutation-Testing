import subprocess
import sys
import threading
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
