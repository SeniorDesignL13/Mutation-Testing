"""Tests for record and replay fixtures (MTLJ-90)."""

import json
import os
from pathlib import Path

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.cache import CachedScore, CacheKeys, ResponseCache
from mtlj.engine.fixtures import (
    FixtureFileError,
    FixtureMissingError,
    FixtureStore,
    load_inputs,
    save_inputs,
)
from mtlj.engine.retry import NO_RETRY
from mtlj.engine.runner import (
    ERROR,
    OK,
    MemorySink,
    build_tasks,
    judge_context,
    run_judge,
)
from mtlj.judges.base import RawScore, VerdictCounts
from mtlj.judges.callable import CallableJudge
from mtlj.operators.base import (
    ExpectedDirection,
    MutationOperator,
    MutationResult,
    OperatorFamily,
    OperatorMetadata,
)


def _record(n: int) -> DatasetRecord:
    return DatasetRecord(
        record_id=f"t_{n}",
        source_dataset="test",
        question=f"Where was conference {n} held?",
        context=[{"passage_id": "p1", "text": f"Conference {n} took place in Paris."}],
        answer=f"Conference {n} was held in Paris.",
    )


class Shout(MutationOperator):
    """Appends '!!!', so every mutant differs from its original."""

    metadata = OperatorMetadata(
        name="shout",
        family=OperatorFamily.DEGRADING,
        expected_direction=ExpectedDirection.DECREASE,
        description="test operator",
    )

    def apply(self, text, context, intensity=1.0, seed=None) -> MutationResult:
        return MutationResult(text, text + "!!!", True, {})


RECORDS = [_record(n) for n in range(3)]
MUTANTS = [Mutant.generate(r, Shout(), 1.0, seed=7) for r in RECORDS]
SAMPLES = 2
SCORINGS = SAMPLES * (len(RECORDS) + len(MUTANTS))


class Counting:
    """A judge function that counts its calls and scores mutants lower, with verdicts."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, input, output, context):
        self.calls += 1
        score = 0.25 if output.endswith("!!!") else 0.75
        return RawScore(score, verdict_counts=VerdictCounts(3, 0, 1), n_claims=4)


def never_called(input, output, context):
    raise AssertionError("replay must not call the judge")


def judge(fn=None, **kwargs) -> CallableJudge:
    # Same name/model/version and no score_fn in its cache settings, so a counting judge and a
    # never-called judge share keys, as a real judge does between a record run and a replay.
    j = CallableJudge(fn or Counting(), name="j", model="m", version="1")
    j.cache_settings = lambda: {}  # type: ignore[method-assign]
    return j


async def run(store, fn=None, *, records=RECORDS, mutants=MUTANTS, samples=SAMPLES):
    sink = MemorySink()
    summary = await run_judge(
        judge(fn), records, mutants, sink, cache=store, samples=samples, retry=NO_RETRY
    )
    return summary, sink.evaluations


def score(value: float = 0.5) -> CachedScore:
    return CachedScore(score=value, raw_score=value, latency_ms=12.0)


async def record(path: Path, fn=None) -> Counting:
    fn = fn or Counting()
    with FixtureStore(path, mode="record") as store:
        await run(store, fn)
    return fn


# --- recording ------------------------------------------------------------------------------


async def test_record_calls_the_judge_and_saves_every_score(tmp_path: Path) -> None:
    path = tmp_path / "fx" / "judge.jsonl"
    fn = await record(path)

    assert fn.calls == SCORINGS
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == SCORINGS
    first = json.loads(lines[0])
    assert first["judge"] == {
        "class": "mtlj.judges.callable.CallableJudge",
        "name": "j",
        "model": "m",
        "version": "1",
        "score_range": [0.0, 1.0],
        "higher_is_better": True,
        "settings": {},
    }
    assert first["result"]["verdict_yes"] == 3
    assert not (tmp_path / "fx" / "judge.jsonl.partial").exists()


async def test_recorded_file_is_sorted_and_identical_when_recorded_again(tmp_path: Path) -> None:
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    await record(first)
    with FixtureStore(second, mode="record") as store:
        # Different order of work: one call at a time.
        await run_judge(
            judge(), RECORDS, MUTANTS, MemorySink(), cache=store, samples=SAMPLES, max_concurrency=1
        )

    text = first.read_text(encoding="utf-8")
    keys = [json.loads(line)["key"] for line in text.splitlines()]
    assert keys == sorted(keys)
    # Latency differs between runs; everything else, order included, must match.
    strip = [{**json.loads(x), "result": None} for x in text.splitlines()]
    again = [{**json.loads(x), "result": None} for x in second.read_text("utf-8").splitlines()]
    assert strip == again
    assert "\r" not in text


async def test_recording_again_only_calls_the_judge_for_missing_scores(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        await run(store, records=RECORDS[:1], mutants=MUTANTS[:1])

    fn = Counting()
    with FixtureStore(path, mode="record") as store:
        summary, _ = await run(store, fn)

    assert fn.calls == SCORINGS - 2 * SAMPLES
    assert summary.cache_hits == 2 * SAMPLES


async def test_refresh_calls_the_judge_for_every_scoring_and_replaces_scores(
    tmp_path: Path,
) -> None:
    path = tmp_path / "judge.jsonl"
    await record(path)

    fn = Counting()
    with FixtureStore(path, mode="record", refresh=True) as store:
        await run(store, fn)

    assert fn.calls == SCORINGS
    assert len(path.read_text(encoding="utf-8").splitlines()) == SCORINGS


async def test_failures_are_never_recorded(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"

    def mutants_fail(input, output, context):
        if output.endswith("!!!"):
            raise ValueError("boom")
        return 0.5

    with FixtureStore(path, mode="record") as store:
        _, rows = await run(store, mutants_fail)

    assert {r.status for r in rows} == {OK, ERROR}
    assert len(path.read_text(encoding="utf-8").splitlines()) == SAMPLES * len(RECORDS)


async def test_scores_survive_a_run_that_dies_and_are_picked_up_next_time(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    store = FixtureStore(path, mode="record")
    await run(store, records=RECORDS[:1], mutants=[])
    # The process dies: no close(), so only the journal holds the scores.
    store._journal.close()  # type: ignore[union-attr]
    assert not path.exists()
    assert len(store.partial_path.read_text(encoding="utf-8").splitlines()) == SAMPLES

    fn = Counting()
    with FixtureStore(path, mode="record") as again:
        assert again.recorded == SAMPLES
        await run(again, fn)

    assert fn.calls == SCORINGS - SAMPLES
    assert len(path.read_text(encoding="utf-8").splitlines()) == SCORINGS
    assert not again.partial_path.exists()


def test_a_partly_written_last_journal_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        store.put("k1", judge(), score())
        store._journal.close()  # type: ignore[union-attr]
        store._journal = None
        store.partial_path.write_text(
            store.partial_path.read_text(encoding="utf-8") + '{"v":1,"key":"k2"', encoding="utf-8"
        )
        store._recorded = 0  # Simulate a crash: nothing is saved on exit.

    again = FixtureStore(path, mode="record")
    assert "k1" in again
    assert "k2" not in again


def test_saving_leaves_the_old_file_whole_if_the_write_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        store.put("k1", judge(), score())
    before = path.read_bytes()

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    store = FixtureStore(path, mode="record")
    store.put("k2", judge(), score())
    with pytest.raises(OSError, match="disk full"):
        store.save()

    assert path.read_bytes() == before
    # The new score is still in the journal, for the next run.
    assert "k2" in store.partial_path.read_text(encoding="utf-8")


def test_nothing_is_written_when_nothing_was_recorded(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record"):
        pass
    assert not path.exists()


# --- replaying ------------------------------------------------------------------------------


async def test_replay_serves_every_score_without_calling_the_judge(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    await record(path)
    recorded = path.read_bytes()

    summary, rows = await run(FixtureStore(path, mode="replay"), never_called)

    assert summary.ok == SCORINGS
    assert summary.cache_hits == SCORINGS
    assert all(r.cached and r.attempts == 0 for r in rows)
    assert {r.score for r in rows if r.kind == "mutant"} == {0.25}
    assert {r.score for r in rows if r.kind == "original"} == {0.75}
    assert all(r.n_claims == 4 for r in rows)
    assert path.read_bytes() == recorded


async def test_replay_fails_before_any_call_when_scores_are_missing(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        await run(store, records=RECORDS[:1], mutants=[])

    sink = MemorySink()
    with pytest.raises(FixtureMissingError) as raised:
        await run_judge(
            judge(never_called),
            RECORDS,
            MUTANTS,
            sink,
            cache=FixtureStore(path, mode="replay"),
            samples=SAMPLES,
        )

    assert raised.value.missing == SCORINGS - SAMPLES
    message = str(raised.value)
    assert f"missing {SCORINGS - SAMPLES} of {SCORINGS}" in message
    assert "original t_1 sample 0" in message
    assert "more" in message
    assert "docs/fixtures.md" in message
    assert sink.evaluations == []


async def test_replay_fails_when_the_judge_version_changed(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    await record(path)
    newer = CallableJudge(never_called, name="j", model="m", version="2")
    newer.cache_settings = lambda: {}  # type: ignore[method-assign]

    with pytest.raises(FixtureMissingError):
        await run_judge(
            newer, RECORDS, MUTANTS, MemorySink(), cache=FixtureStore(path, mode="replay")
        )


async def test_replay_fails_when_a_text_changed(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    await record(path)
    edited = [RECORDS[0].model_copy(update={"answer": "Conference 0 was held in Rome."})]

    with pytest.raises(FixtureMissingError, match="original t_0"):
        await run(FixtureStore(path, mode="replay"), never_called, records=edited, mutants=[])


def test_replay_needs_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Record one first"):
        FixtureStore(tmp_path / "nope.jsonl", mode="replay")


def test_replay_never_records(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="doesn't record"):
        FixtureStore(path, mode="replay").put("k", judge(), score())


def test_replay_ignores_a_leftover_journal(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        store.put("k1", judge(), score())
    (tmp_path / "judge.jsonl.partial").write_text(
        (path.read_text(encoding="utf-8")).replace('"k1"', '"k2"'), encoding="utf-8"
    )
    assert "k2" not in FixtureStore(path, mode="replay")


def test_replay_store_is_strict_and_record_store_and_cache_are_not(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    path.write_text("", encoding="utf-8")
    assert FixtureStore(path, mode="replay").strict
    assert not FixtureStore(path, mode="record").strict
    with ResponseCache(tmp_path / "c.sqlite") as cache:
        assert not cache.strict


def test_keys_match_the_response_cache(tmp_path: Path) -> None:
    j = judge()
    key = CacheKeys(j)(RECORDS[0].question, RECORDS[0].answer, judge_context(RECORDS[0].context), 0)
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        store.put(key, j, score(0.4))
    assert FixtureStore(path, mode="replay").get(key) == score(0.4)


# --- bad files and arguments ----------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"v":1,"key":"k"}',
        '{"v":2,"key":"k","judge":{"name":"j","model":"m","version":"1"},'
        '"result":{"score":0.5,"raw_score":0.5,"latency_ms":1.0}}',
        '{"v":1,"key":"k","judge":{"name":"j","model":"m","version":"1"},'
        '"result":{"score":1.5,"raw_score":0.5,"latency_ms":1.0}}',
        '{"v":1,"key":"k","extra":1,"judge":{"name":"j","model":"m","version":"1"},'
        '"result":{"score":0.5,"raw_score":0.5,"latency_ms":1.0}}',
    ],
    ids=["not-json", "incomplete", "other-format", "bad-score", "unknown-field"],
)
def test_a_bad_line_is_refused_with_its_line_number(tmp_path: Path, line: str) -> None:
    path = tmp_path / "judge.jsonl"
    path.write_text("\n" + line + "\n", encoding="utf-8")
    with pytest.raises(FixtureFileError, match="line 2"):
        FixtureStore(path, mode="replay")


def test_two_different_scores_for_one_key_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        store.put("k", judge(), score(0.5))
    line = path.read_text(encoding="utf-8")
    path.write_text(line + line.replace("0.5", "0.6"), encoding="utf-8")

    with pytest.raises(FixtureFileError, match="line 2: a second, different score"):
        FixtureStore(path, mode="replay")


def test_an_exact_duplicate_line_is_allowed(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    with FixtureStore(path, mode="record") as store:
        store.put("k", judge(), score())
    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")
    assert len(FixtureStore(path, mode="replay")) == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"mode": "play"}, "mode must be"), ({"mode": "replay", "refresh": True}, "refresh only")],
)
def test_bad_arguments_are_refused(tmp_path: Path, kwargs, message) -> None:
    path = tmp_path / "judge.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        FixtureStore(path, **kwargs)


def test_missing_error_survives_pickling() -> None:
    import pickle

    error = pickle.loads(pickle.dumps(FixtureMissingError("gone", missing=3)))
    assert (str(error), error.missing) == ("gone", 3)


# --- the recorded judge, stale scores and saved inputs ---------------------------------------


def versioned(fn=None, version: str = "1") -> CallableJudge:
    j = judge(fn)
    j.version = version
    return j


async def record_as(path: Path, version: str, fn=None) -> None:
    with FixtureStore(path, mode="record") as store:
        await run_judge(
            versioned(fn, version), RECORDS, MUTANTS, MemorySink(), cache=store, samples=SAMPLES
        )


async def test_judge_is_the_identity_the_scores_were_recorded_with(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    await record(path)

    recorded = FixtureStore(path, mode="replay").judge

    assert (recorded.name, recorded.model, recorded.version) == ("j", "m", "1")


def test_judge_is_none_when_nothing_is_recorded(tmp_path: Path) -> None:
    assert FixtureStore(tmp_path / "fx.jsonl", mode="record").judge is None


async def test_scores_from_two_versions_are_refused_as_unfinished(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    await record_as(path, "1")
    await record_as(path, "2")  # a re-record after an upgrade, without dropping the old scores

    with pytest.raises(FixtureFileError, match="several judges or versions"):
        _ = FixtureStore(path, mode="replay").judge


async def test_replay_as_the_recorded_judge_survives_an_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    await record_as(path, "1")
    store = FixtureStore(path, mode="replay")

    # The installed library moved on: its version gives other keys, so replay can't find them.
    with pytest.raises(FixtureMissingError):
        await run_judge(versioned(never_called, "2"), RECORDS, MUTANTS, MemorySink(), cache=store)
    # Replayed as the judge that was recorded, every score is found and nothing is called.
    sink = MemorySink()
    summary = await run_judge(
        versioned(never_called, store.judge.version),
        RECORDS,
        MUTANTS,
        sink,
        cache=store,
        samples=SAMPLES,
    )

    assert summary.cache_hits == SCORINGS


async def test_retain_drops_scores_outside_the_plan_and_saves(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    await record_as(path, "1")
    await record_as(path, "2")
    keys = CacheKeys(versioned(version="2"))
    current = {
        keys(t.input, t.output, judge_context(t.passages), t.sample_index)
        for t in build_tasks(RECORDS, MUTANTS, SAMPLES)
    }

    with FixtureStore(path, mode="record") as store:
        dropped = store.retain(current)

    assert dropped == SCORINGS  # every version-1 score
    replay = FixtureStore(path, mode="replay")
    assert len(replay) == SCORINGS
    assert replay.judge.version == "2"


def test_retain_is_refused_in_replay(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="doesn't change its file"):
        FixtureStore(path, mode="replay").retain(set())


async def test_saved_inputs_replay_exactly_what_was_recorded(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    await record(path)
    save_inputs(tmp_path / "inputs", RECORDS, MUTANTS)

    records, mutants = load_inputs(tmp_path / "inputs")
    summary, _ = await run(
        FixtureStore(path, mode="replay"), never_called, records=records, mutants=mutants
    )

    assert (records, mutants) == (RECORDS, MUTANTS)
    assert summary.cache_hits == SCORINGS


def test_mutants_from_the_default_battery_survive_saving(tmp_path: Path) -> None:
    from mtlj.engine.generator import default_battery, generate_mutants

    mutants = generate_mutants(RECORDS, default_battery(), base_seed=0).mutants
    save_inputs(tmp_path, RECORDS, mutants)

    assert load_inputs(tmp_path) == (RECORDS, mutants)


def test_a_bad_input_line_is_refused_with_its_line_number(tmp_path: Path) -> None:
    save_inputs(tmp_path, RECORDS, MUTANTS)
    with (tmp_path / "mutants.jsonl").open("a", encoding="utf-8") as file:
        file.write("{not json\n")

    with pytest.raises(FixtureFileError, match=f"line {len(MUTANTS) + 1}"):
        load_inputs(tmp_path)


def test_a_torn_journal_line_is_cut_off_so_new_scores_stay_readable(tmp_path: Path) -> None:
    path = tmp_path / "judge.jsonl"
    store = FixtureStore(path, mode="record")
    store.put("k1", judge(), score())
    store.close(save=False)
    with store.partial_path.open("a", encoding="utf-8", newline="\n") as journal:
        journal.write('{"v":1,"key":"k2"')  # the process died part way through this line

    resumed = FixtureStore(path, mode="record")
    resumed.put("k3", judge(), score())
    resumed.close(save=False)  # and died again before saving

    again = FixtureStore(path, mode="record")
    assert "k1" in again and "k3" in again
    assert "k2" not in again


async def test_closing_without_saving_leaves_the_file_and_keeps_the_journal(tmp_path) -> None:
    path = tmp_path / "fx.jsonl"
    await record(path)
    before = path.read_bytes()

    store = FixtureStore(path, mode="record")
    store.put("new", judge(), score())
    store.close(save=False)

    assert path.read_bytes() == before
    assert "new" in FixtureStore(path, mode="record")  # picked up from the journal
    assert "new" not in FixtureStore(path, mode="replay")


async def test_replay_as_takes_the_recorded_version(tmp_path: Path) -> None:
    path = tmp_path / "fx.jsonl"
    await record_as(path, "1")

    replayed = FixtureStore(path, mode="replay").replay_as(versioned(never_called, "9"))

    assert replayed.version == "1"


async def test_replay_as_refuses_a_judge_that_differs_in_more_than_its_version(tmp_path) -> None:
    path = tmp_path / "fx.jsonl"
    await record_as(path, "1")
    other = versioned(never_called, "1")
    other.model = "another-model"

    with pytest.raises(FixtureFileError, match="model: 'm' != 'another-model'"):
        FixtureStore(path, mode="replay").replay_as(other)


def test_replay_as_needs_fixtures(tmp_path: Path) -> None:
    with pytest.raises(FixtureFileError, match="holds no fixtures"):
        FixtureStore(tmp_path / "fx.jsonl", mode="record").replay_as(judge())
