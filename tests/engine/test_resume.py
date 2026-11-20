"""Resuming a stopped audit (MTLJ-91): runs killed part way, then resumed, with stub judges.

The acceptance test is the first one: a run in its own process is killed mid-flight with
SIGKILL, resumed, and must leave the same ``results.json``, byte for byte, as a run that was
never stopped. The judge's scores depend only on the text it's given, so the two runs' scores
are the same whatever order calls finish in.
"""

from __future__ import annotations

import json
import math
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mtlj.data.records import DatasetRecord
from mtlj.engine.audit import (
    EVALUATIONS_FILE,
    INTERRUPTED,
    check_can_start,
    check_run_dir,
    prepare_resume,
    resume_audit,
    run_audit,
)
from mtlj.engine.generator import GenerationResult, default_battery, generate_mutants
from mtlj.engine.results import RESULTS_FILE, Limits, read_results
from mtlj.engine.run_state import (
    AUDIT_STATE_FILE,
    LOCK_FILE,
    SUPERSEDED_FILE,
    AuditState,
    JudgeFingerprint,
    NotResumableError,
    RunInUseError,
    lock_run,
    read_audit_state,
)
from mtlj.engine.runner import ERROR, Evaluation, RunLimits, read_evaluations, scoring_key
from mtlj.judges.callable import CallableJudge

posix_signals = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals")

EXAMPLE_RECORDS = Path(__file__).parent.parent / "data" / "example_records.jsonl"
SEED = 1
# Enough scorings that a run is still going when it's stopped part way.
KILL_SAMPLES = 20

# The judge's scoring, shared with the process that's killed: a score from the text alone.
SCORE = """
import hashlib

def score(input, output, context):
    digest = hashlib.sha256(output.encode("utf-8")).digest()
    return 0.3 + digest[0] / 400
"""
_namespace: dict = {}
exec(SCORE, _namespace)
score = _namespace["score"]

# A run (or a resume) slow enough to kill part way, in its own process.
KILLABLE = """
import sys, time
from pathlib import Path
from mtlj.data.records import DatasetRecord
from mtlj.engine.audit import resume_audit, run_audit
from mtlj.engine.generator import default_battery, generate_mutants
from mtlj.judges.callable import CallableJudge

mode, records_path, run_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
samples = int(sys.argv[4])
exec(sys.argv[5])

def slow(input, output, context):
    time.sleep(0.01)
    return score(input, output, context)

judge = CallableJudge(slow, name="stub", model="m", version="1")
if mode == "run":
    records = [
        DatasetRecord.model_validate_json(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
    ]
    generation = generate_mutants(records, default_battery(), base_seed=SEED)
    run_audit(generation, judge, run_dir, dataset="test", samples=samples)
else:
    resume_audit(run_dir, judge)
""".replace("SEED", str(SEED))


def stub(version: str = "1", calls: list | None = None, fail_after: int | None = None):
    """The judge both runs use. ``calls`` collects each call; after ``fail_after`` it's down."""
    lock = threading.Lock()

    def scoring(input, output, context):
        with lock:
            if calls is not None:
                calls.append(output)
            n = len(calls) if calls is not None else 0
        if fail_after is not None and n > fail_after:
            raise PermissionError("invalid API key")  # permanent: no retry waits
        return score(input, output, context)

    return CallableJudge(scoring, name="stub", model="m", version=version)


@pytest.fixture(scope="module")
def generation() -> GenerationResult:
    records = [
        DatasetRecord.model_validate_json(line)
        for line in EXAMPLE_RECORDS.read_text(encoding="utf-8").splitlines()
    ]
    return generate_mutants(records, default_battery(), base_seed=SEED)


@pytest.fixture(scope="module")
def reference(generation, tmp_path_factory) -> Path:
    """A run with KILL_SAMPLES samples that was never stopped."""
    run_dir = tmp_path_factory.mktemp("reference") / "run"
    run_audit(generation, stub(), run_dir, dataset="test", samples=KILL_SAMPLES)
    assert read_results(run_dir).run.status == "complete"
    return run_dir


def start(mode: str, run_dir: Path, samples: int = KILL_SAMPLES) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            KILLABLE,
            mode,
            str(EXAMPLE_RECORDS),
            str(run_dir),
            str(samples),
            SCORE,
        ]
    )


def wait_for_rows(path: Path, rows: int, process: subprocess.Popen, timeout: float = 120) -> None:
    """Wait until ``path`` has ``rows`` complete rows, with the process still scoring."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_bytes().count(b"\n") >= rows:
            return
        if process.poll() is not None:
            raise AssertionError(f"the process ended ({process.returncode}) before it was stopped")
        time.sleep(0.005)
    process.kill()
    raise AssertionError(f"{path} never got {rows} rows")


def results_bytes(run_dir: Path) -> bytes:
    return (run_dir / RESULTS_FILE).read_bytes()


def scores_by_scoring(run_dir: Path) -> dict:
    return {
        scoring_key(e): (e.status, e.score) for e in read_evaluations(run_dir / EVALUATIONS_FILE)
    }


def assert_same_as(run_dir: Path, reference: Path) -> None:
    assert results_bytes(run_dir) == results_bytes(reference)
    assert scores_by_scoring(run_dir) == scores_by_scoring(reference)
    rows = read_evaluations(run_dir / EVALUATIONS_FILE)
    assert len(rows) == len({scoring_key(e) for e in rows})  # one row per scoring


# --------------------------------------------------
# Killed mid-flight, then resumed (the ticket's acceptance test)
# --------------------------------------------------


def test_a_killed_run_resumes_to_the_results_an_uninterrupted_run_gives(
    tmp_path: Path, reference: Path
):
    run_dir = tmp_path / "run"
    process = start("run", run_dir)
    wait_for_rows(run_dir / EVALUATIONS_FILE, 100, process)

    # While it's scoring, nothing else may score into its directory.
    with pytest.raises(RunInUseError, match="in use by another mtlj process"):
        resume_audit(run_dir, stub())

    process.kill()  # SIGKILL: no handler runs, nothing is tidied up
    process.wait(timeout=30)
    assert not (run_dir / RESULTS_FILE).exists()
    stored = len(read_evaluations(run_dir / EVALUATIONS_FILE))
    total = len(scores_by_scoring(reference))
    assert 100 <= stored < total

    calls: list = []
    results = resume_audit(run_dir, stub(calls=calls))

    assert results.run.status == "complete"
    # Only what was missing was scored: calls in flight when the kill came, and a row it cut
    # short, weren't stored, so they're scored again.
    assert len(calls) == total - stored
    assert_same_as(run_dir, reference)


def test_a_row_torn_by_the_kill_is_cut_off_and_scored_again(tmp_path: Path, reference: Path):
    run_dir = tmp_path / "run"
    process = start("run", run_dir)
    wait_for_rows(run_dir / EVALUATIONS_FILE, 60, process)
    process.kill()
    process.wait(timeout=30)
    path = run_dir / EVALUATIONS_FILE
    data = path.read_bytes()
    if data.endswith(b"\n"):  # make sure the kill landed mid-row, as it can
        last = data[:-1].rsplit(b"\n", 1)[-1]
        path.write_bytes(data + last[: len(last) // 2])

    plan = prepare_resume(run_dir, stub())
    assert plan.torn_tail

    resume_audit(run_dir, stub())

    assert_same_as(run_dir, reference)


def test_a_resume_that_is_killed_in_turn_resumes_again(tmp_path: Path, reference: Path):
    run_dir = tmp_path / "run"
    first = start("run", run_dir)
    wait_for_rows(run_dir / EVALUATIONS_FILE, 80, first)
    first.kill()
    first.wait(timeout=30)

    second = start("resume", run_dir)
    wait_for_rows(run_dir / EVALUATIONS_FILE, 250, second)
    second.kill()
    second.wait(timeout=30)

    resume_audit(run_dir, stub())

    assert_same_as(run_dir, reference)


@posix_signals
@pytest.mark.parametrize("stop", ["SIGTERM", "SIGINT"])
def test_a_run_stopped_by_a_signal_resumes_to_the_same_results(
    tmp_path: Path, reference: Path, stop: str
):
    run_dir = tmp_path / "run"
    process = start("run", run_dir)
    wait_for_rows(run_dir / EVALUATIONS_FILE, 100, process)
    process.send_signal(getattr(signal, stop))
    assert process.wait(timeout=30) == 0
    assert read_results(run_dir).run.reason == INTERRUPTED

    resume_audit(run_dir, stub())

    assert_same_as(run_dir, reference)


# --------------------------------------------------
# What a resume scores
# --------------------------------------------------


def complete_run(generation, run_dir: Path, samples: int = 2) -> Path:
    run_audit(generation, stub(), run_dir, dataset="test", samples=samples)
    return run_dir


def cut_short(run_dir: Path, keep: int, replace: dict[int, dict] | None = None) -> list[Evaluation]:
    """Leave ``run_dir`` as a run stopped after ``keep`` rows, with rows ``replace``d by errors."""
    path = run_dir / EVALUATIONS_FILE
    rows = read_evaluations(path)[:keep]
    for index, fields in (replace or {}).items():
        rows[index] = rows[index].model_copy(
            update={"status": ERROR, "score": None, "raw_score": None, **fields}
        )
    path.write_text("".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
    (run_dir / RESULTS_FILE).unlink()
    return rows


TRANSIENT_ERROR = {"error_type": "TimeoutError", "error": "timed out", "error_kind": "transient"}
PERMANENT_ERROR = {"error_type": "PermissionError", "error": "no", "error_kind": "permanent"}


def test_only_the_missing_scorings_are_scored_and_the_results_match(generation, tmp_path: Path):
    whole = complete_run(generation, tmp_path / "whole")
    run_dir = complete_run(generation, tmp_path / "run")
    kept = cut_short(run_dir, keep=17)

    calls: list = []
    summaries = []
    resume_audit(run_dir, stub(calls=calls), on_summary=summaries.append)

    total = len(scores_by_scoring(whole))
    assert len(calls) == total - 17
    assert summaries[0].already_stored == 17
    assert summaries[0].total == total - 17
    # The rows that were kept are untouched, at the top of the file.
    assert read_evaluations(run_dir / EVALUATIONS_FILE)[:17] == kept
    assert_same_as(run_dir, whole)


def test_a_judge_that_went_down_is_resumed_once_it_is_back(generation, tmp_path: Path):
    whole = complete_run(generation, tmp_path / "whole")
    run_dir = tmp_path / "run"
    results = run_audit(
        generation, stub(calls=[], fail_after=10), run_dir, dataset="test", samples=2
    )
    assert results.run.status == "aborted"
    failed = [e for e in read_evaluations(run_dir / EVALUATIONS_FILE) if e.status == ERROR]
    assert failed and all(e.error_kind == "permanent" for e in failed)  # a revoked key

    # The key is fixed. Permanent failures stay failed unless asked...
    assert len(prepare_resume(run_dir, stub()).retried) == 0
    # ...and with all, they're scored again, and the run ends as if it never failed.
    resume_audit(run_dir, stub(), retry_errors="all")

    assert_same_as(run_dir, whole)
    assert len(read_evaluations(run_dir / SUPERSEDED_FILE)) == len(failed)


def test_transient_failures_are_scored_again_and_their_rows_kept_aside(generation, tmp_path):
    whole = complete_run(generation, tmp_path / "whole")
    run_dir = complete_run(generation, tmp_path / "run")
    rows = cut_short(run_dir, keep=20, replace={3: TRANSIENT_ERROR, 9: TRANSIENT_ERROR})

    resume_audit(run_dir, stub())

    assert_same_as(run_dir, whole)
    assert read_evaluations(run_dir / SUPERSEDED_FILE) == [rows[3], rows[9]]


@pytest.mark.parametrize(
    ("mode", "retried"),
    [("none", set()), ("transient", {3}), ("all", {3, 5})],
)
def test_which_failures_are_scored_again(generation, tmp_path: Path, mode: str, retried: set):
    run_dir = complete_run(generation, tmp_path / "run")
    rows = cut_short(run_dir, keep=20, replace={3: TRANSIENT_ERROR, 5: PERMANENT_ERROR})

    plan = prepare_resume(run_dir, stub(), retry_errors=mode)
    results = resume_audit(run_dir, stub(), retry_errors=mode)

    assert {rows.index(e) for e in plan.retried} == retried
    assert results.run.scorings.errors == 2 - len(retried)
    assert results.run.status == "complete"


def test_a_row_from_before_error_kind_was_kept_counts_as_transient(generation, tmp_path):
    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=10, replace={2: {"error_type": "X", "error": "?", "error_kind": None}})

    assert len(prepare_resume(run_dir, stub()).retried) == 1


def test_a_complete_run_resumes_without_calling_the_judge(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    before = results_bytes(run_dir)
    (run_dir / RESULTS_FILE).unlink()  # e.g. killed while results.json was being written

    calls: list = []
    resume_audit(run_dir, stub(calls=calls))

    assert calls == []
    assert results_bytes(run_dir) == before


def test_a_run_killed_before_its_first_score_resumes_from_nothing(generation, tmp_path: Path):
    whole = complete_run(generation, tmp_path / "whole")
    run_dir = complete_run(generation, tmp_path / "run")
    (run_dir / EVALUATIONS_FILE).unlink()
    (run_dir / RESULTS_FILE).unlink()

    resume_audit(run_dir, stub())

    assert_same_as(run_dir, whole)


def test_preparing_a_resume_changes_nothing(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=12, replace={4: TRANSIENT_ERROR})
    with (run_dir / EVALUATIONS_FILE).open("ab") as file:
        file.write(b'{"kind": "orig')
    before = {p.name: p.read_bytes() for p in run_dir.iterdir()}

    plan = prepare_resume(run_dir, stub())

    assert (len(plan.kept), len(plan.retried), plan.torn_tail) == (11, 1, b'{"kind": "orig')
    assert plan.remaining == plan.total - 11
    assert {p.name: p.read_bytes() for p in run_dir.iterdir()} == before


def test_the_limits_default_to_the_runs_and_can_be_changed(generation, tmp_path: Path):
    run_dir = tmp_path / "run"
    run_audit(generation, stub(), run_dir, dataset="test", samples=2, max_concurrency=3)
    cut_short(run_dir, keep=5)

    assert resume_audit(run_dir, stub()).run.limits == Limits(max_concurrency=3, rate_limit=None)

    cut_short(run_dir, keep=5)
    changed = resume_audit(run_dir, stub(), max_concurrency=1, rate_limit=6000)
    assert changed.run.limits == Limits(max_concurrency=1, rate_limit=6000)
    assert changed.run.status == "complete"


def test_a_resume_stopped_with_ctrl_c_leaves_an_aborted_results_file(
    generation, tmp_path: Path, monkeypatch
):
    from mtlj.engine import audit

    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=5)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(audit, "run_judge_sync", interrupted)
    results = resume_audit(run_dir, stub())

    assert results.run.status == "aborted"
    assert results.run.reason == INTERRUPTED
    assert results.run.scorings.total == 5


# --------------------------------------------------
# What a resume refuses, before anything changes
# --------------------------------------------------


def unchanged(run_dir: Path):
    """Snapshot ``run_dir``; calling the result checks nothing in it changed."""
    before = {p.name: p.read_bytes() for p in run_dir.iterdir()}
    return lambda: {p.name: p.read_bytes() for p in run_dir.iterdir()} == before


def test_a_different_judge_is_refused(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=5)
    same = unchanged(run_dir)
    calls: list = []

    with pytest.raises(NotResumableError, match=r"different judge \(version: '1' != '2'\)"):
        resume_audit(run_dir, stub(version="2", calls=calls))

    assert calls == []
    assert same()


def test_a_judge_with_other_settings_is_refused(tmp_path: Path):
    from mtlj.judges.base import Judge

    class Tempered(Judge):
        name, model, version = "tempered", "m", "1"
        score_range = (0.0, 1.0)
        higher_is_better = True

        def __init__(self, temperature: float) -> None:
            self.temperature = temperature

        def score_raw(self, input, output, context):
            return 0.5

    records = [
        DatasetRecord.model_validate_json(line)
        for line in EXAMPLE_RECORDS.read_text(encoding="utf-8").splitlines()
    ]
    run_dir = tmp_path / "run"
    run_audit(GenerationResult(records, [], []), Tempered(0.0), run_dir, dataset="t", samples=2)

    with pytest.raises(NotResumableError, match=r"settings: its cache_settings\(\) differ"):
        prepare_resume(run_dir, Tempered(0.7))
    prepare_resume(run_dir, Tempered(0.0))


def edit_rows(run_dir: Path, edit) -> None:
    path = run_dir / EVALUATIONS_FILE
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    edit(rows)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda rows: rows[3].update(target_id="from-another-run"), "isn't one of this run's"),
        (lambda rows: rows[3].update(sample_index=99), "isn't one of this run's"),
        (lambda rows: rows[3].update(record_id="other"), "isn't one of this run's"),
        (lambda rows: rows.append(dict(rows[0])), "is stored twice"),
        (lambda rows: rows[3].update(version="0.9"), "was scored by"),
        (lambda rows: rows[3].update(score="high"), "line 4 isn't a valid evaluation"),
    ],
)
def test_stored_rows_that_do_not_belong_are_refused(generation, tmp_path: Path, edit, message):
    run_dir = complete_run(generation, tmp_path / "run")
    edit_rows(run_dir, edit)
    same = unchanged(run_dir)

    with pytest.raises(NotResumableError, match=message):
        resume_audit(run_dir, stub())

    assert same()


def test_a_run_whose_setup_never_finished_is_refused(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    (run_dir / AUDIT_STATE_FILE).unlink()

    with pytest.raises(NotResumableError, match="its setup never finished"):
        resume_audit(run_dir, stub())


def test_a_run_whose_generation_files_are_broken_is_refused(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    (run_dir / "mutants.jsonl").write_text("not json\n", encoding="utf-8")

    with pytest.raises(NotResumableError, match=r"mutants\.jsonl can't be read back"):
        resume_audit(run_dir, stub())


def test_an_audit_json_from_a_newer_mtlj_is_refused(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    state = json.loads((run_dir / AUDIT_STATE_FILE).read_text(encoding="utf-8"))
    state["schema_version"] = 99
    (run_dir / AUDIT_STATE_FILE).write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(NotResumableError, match="schema version 99"):
        prepare_resume(run_dir, stub())


def test_a_missing_directory_is_refused(tmp_path: Path):
    with pytest.raises(NotResumableError, match="isn't a run directory"):
        prepare_resume(tmp_path / "nowhere", stub())


def test_an_unknown_retry_mode_is_refused(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    with pytest.raises(ValueError, match="retry_errors must be one of"):
        prepare_resume(run_dir, stub(), retry_errors="some")


def test_a_run_in_use_is_refused(generation, tmp_path: Path):
    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=5)

    with lock_run(run_dir), pytest.raises(RunInUseError):
        resume_audit(run_dir, stub())
    resume_audit(run_dir, stub())  # free again once the other process lets go


def test_a_new_run_cannot_take_a_directory_in_use(generation, tmp_path: Path):
    run_dir = tmp_path / "run"
    with lock_run(run_dir), pytest.raises(RunInUseError):
        run_audit(generation, stub(), run_dir, dataset="test", samples=2)
    assert [p.name for p in run_dir.iterdir()] == [LOCK_FILE]


def test_a_directory_holding_only_a_lock_file_counts_as_empty(tmp_path: Path):
    (tmp_path / LOCK_FILE).touch()
    check_run_dir(tmp_path)


# --------------------------------------------------
# audit.json
# --------------------------------------------------


def test_a_run_saves_what_resuming_it_needs(generation, tmp_path: Path):
    run_dir = tmp_path / "run"
    run_audit(generation, stub(), run_dir, dataset="gold", samples=3, rate_limit=600)

    state = read_audit_state(run_dir)

    assert state is not None
    assert (state.dataset, state.samples) == ("gold", 3)
    assert (state.limits.max_concurrency, state.limits.rate_limit) == (4, 600)
    assert (state.judge.name, state.judge.model, state.judge.version) == ("stub", "m", "1")
    assert state.judge.judge_class == "mtlj.judges.callable.CallableJudge"
    assert state.judge.settings_sha256 is None  # a function can't be told apart by settings
    assert (run_dir / AUDIT_STATE_FILE).read_bytes().endswith(b"\n")


def test_a_run_without_audit_json_resumes_from_a_state_given_for_it(generation, tmp_path):
    whole = complete_run(generation, tmp_path / "whole")
    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=9)
    (run_dir / AUDIT_STATE_FILE).unlink()
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    del manifest["operators"], manifest["families"]  # and a run.json from before MTLJ-91
    (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    state = AuditState.for_run(stub(), dataset="test", samples=2, limits=RunLimits(4, None))

    resume_audit(run_dir, stub(), state=state)

    assert_same_as(run_dir, whole)


def test_audit_json_keeps_a_hash_of_the_judges_settings_not_the_settings(tmp_path: Path):
    from mtlj.judges.base import Judge

    class Hosted(Judge):
        name, model, version = "hosted", "m", "1"
        score_range = (0.0, 1.0)
        higher_is_better = True

        def __init__(self) -> None:
            # Public attributes are cache settings by default, secrets included.
            self.api_key = "sk-secret-123"
            self.endpoint = "https://example.test/score?token=tok-456"

        def score_raw(self, input, output, context):
            return 0.5

    records = [
        DatasetRecord.model_validate_json(line)
        for line in EXAMPLE_RECORDS.read_text(encoding="utf-8").splitlines()
    ]
    run_dir = tmp_path / "run"
    run_audit(GenerationResult(records, [], []), Hosted(), run_dir, dataset="t", samples=2)

    text = (run_dir / AUDIT_STATE_FILE).read_text(encoding="utf-8")
    assert "sk-secret-123" not in text
    assert "tok-456" not in text
    assert len(read_audit_state(run_dir).judge.settings_sha256) == 64


def test_a_judge_that_cannot_be_recorded_is_refused_before_anything_is_written(
    generation, tmp_path: Path
):
    judge = CallableJudge(score, name="stub", model="m", version="1", score_range=(0, math.inf))
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match=r"can't be recorded in audit\.json"):
        check_can_start(generation, judge, run_dir, samples=2)
    with pytest.raises(ValueError, match=r"can't be recorded in audit\.json"):
        run_audit(generation, judge, run_dir, dataset="test", samples=2)

    assert not run_dir.exists()


def test_a_whole_row_that_lost_only_its_newline_is_kept(generation, tmp_path: Path):
    whole = complete_run(generation, tmp_path / "whole")
    run_dir = complete_run(generation, tmp_path / "run")
    rows = cut_short(run_dir, keep=10)
    path = run_dir / EVALUATIONS_FILE
    path.write_bytes(path.read_bytes().rstrip(b"\n"))  # died between the row and its newline

    plan = prepare_resume(run_dir, stub())
    calls: list = []
    resume_audit(run_dir, stub(calls=calls))

    assert (plan.unterminated, plan.torn_tail, len(plan.kept)) == (True, b"", 10)
    assert len(calls) == len(scores_by_scoring(whole)) - 10  # the tenth wasn't paid for twice
    assert read_evaluations(path)[:10] == rows
    assert_same_as(run_dir, whole)


def test_an_unverified_fingerprint_still_checks_name_model_and_version(generation, tmp_path):
    run_dir = complete_run(generation, tmp_path / "run")
    cut_short(run_dir, keep=5)
    (run_dir / AUDIT_STATE_FILE).unlink()

    def legacy(judge):
        state = AuditState.for_run(judge, dataset="test", samples=2, limits=RunLimits(4, None))
        return state.model_copy(update={"judge": JudgeFingerprint.unverified(judge)})

    # The rows were scored by version 1, whatever the state given says.
    with pytest.raises(NotResumableError, match="was scored by"):
        prepare_resume(run_dir, stub(version="2"), state=legacy(stub(version="2")))
    prepare_resume(run_dir, stub(), state=legacy(stub()))
