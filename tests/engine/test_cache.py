"""Tests for the content-addressed response cache (MTLJ-93)."""

import logging
import math
import sqlite3
import threading
from collections import Counter
from pathlib import Path

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine import cache as cache_module
from mtlj.engine.cache import (
    CACHE_KEY_VERSION,
    CachedScore,
    CacheKeys,
    ResponseCache,
    judge_identity,
)
from mtlj.engine.runner import (
    ERROR,
    OK,
    JsonlSink,
    MemorySink,
    judge_context,
    read_evaluations,
    run_judge,
    run_judge_sync,
)
from mtlj.judges.base import Judge, RawScore, VerdictCounts
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
        context=[
            {"passage_id": "p1", "text": "The annual conference took place in Paris."},
            {"passage_id": "p2", "text": f"Conference {n} had many visitors.", "title": "News"},
        ],
        answer=f"The conference number {n} was held in Paris last spring.",
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
TEXTS = len(RECORDS) + len(MUTANTS)


class Noisy:
    """Scores each text a little differently on every call, like a real judge, and counts calls.

    A text's k-th call gets the k-th offset, so the scores don't depend on which worker is first.
    """

    OFFSETS = (0.0, 0.03, -0.03, 0.02, -0.02, 0.01, -0.01, 0.04)

    def __init__(self, base: float = 0.5) -> None:
        self.base = base
        self.calls: Counter[str] = Counter()
        self.lock = threading.Lock()

    def __call__(self, input, output, context) -> RawScore:
        with self.lock:
            k = self.calls[output]
            self.calls[output] += 1
        return RawScore(
            self.base + self.OFFSETS[k % len(self.OFFSETS)],
            verdict_counts=VerdictCounts(yes=3, borderline=1, no=k % 2),
            n_claims=5,
        )

    @property
    def total(self) -> int:
        return sum(self.calls.values())


class SettingsJudge(Judge):
    """A judge with settings that change its scores, kept as public attributes."""

    score_range = (0.0, 1.0)

    def __init__(self, score_fn, *, strict: bool = False, prompt: str = "Rate the answer.") -> None:
        self.name = "stub"
        self.model = "stub-model"
        self.version = "1.0"
        self.strict = strict
        self.prompt = prompt
        self._score_fn = score_fn  # private, so not a setting

    def score_raw(self, input, output, context):
        return self._score_fn(input, output, context)


@pytest.fixture
def cache(tmp_path: Path):
    with ResponseCache(tmp_path / "cache" / "responses.sqlite3") as cache:
        yield cache


async def run(judge, cache, *, samples=2, sink=None, **kwargs):
    sink = sink if sink is not None else MemorySink()
    summary = await run_judge(judge, RECORDS, MUTANTS, sink, cache=cache, samples=samples, **kwargs)
    return summary, sink


def scores(sink: MemorySink) -> dict:
    return {(e.target_id, e.sample_index): e.score for e in sink.evaluations}


# --------------------------------------------------
# The key
# --------------------------------------------------

CONTEXT = judge_context(RECORDS[0].context)


def key(judge=None, input="q", output="a", context=CONTEXT, sample_index=0) -> str:
    return CacheKeys(judge or SettingsJudge(Noisy()))(input, output, context, sample_index)


def test_the_same_call_has_the_same_key() -> None:
    assert key() == key()
    assert len(key()) == 64 and int(key(), 16) >= 0


def test_a_different_judge_model_version_or_setting_has_a_different_key() -> None:
    def changed(**attrs) -> str:
        judge = SettingsJudge(Noisy())
        for name, value in attrs.items():
            setattr(judge, name, value)
        return key(judge)

    base = key()
    variants = [
        changed(name="other"),
        changed(model="gpt-4o"),
        changed(version="1.1"),
        changed(strict=True),
        changed(prompt="Rate the answer strictly."),
        changed(score_range=(0.0, 10.0)),
        changed(higher_is_better=False),
    ]
    assert len({base, *variants}) == len(variants) + 1


def test_a_different_judge_class_has_a_different_key() -> None:
    class Renamed(SettingsJudge):
        pass

    assert key(Renamed(Noisy())) != key()


def test_a_different_input_output_context_or_sample_has_a_different_key() -> None:
    passages = CONTEXT["passages"]
    variants = [
        key(input="q2"),
        key(output="a!"),
        key(context={"passages": passages[::-1]}),  # order matters to a judge
        key(context={"passages": [{**passages[0], "text": "Elsewhere."}, passages[1]]}),
        key(context={"passages": [passages[0], {**passages[1], "title": None}]}),
        key(sample_index=1),
    ]
    assert len({key(), *variants}) == len(variants) + 1


def test_the_key_ignores_the_order_of_dict_keys() -> None:
    reordered = {"passages": [dict(reversed(list(p.items()))) for p in CONTEXT["passages"]]}

    assert key(context=reordered) == key()


def test_bumping_the_key_version_retires_every_key(monkeypatch) -> None:
    before = key()
    monkeypatch.setattr(cache_module, "CACHE_KEY_VERSION", CACHE_KEY_VERSION + 1)

    assert key() != before


def test_settings_default_to_public_instance_attributes() -> None:
    judge = SettingsJudge(Noisy(), strict=True)

    assert judge.cache_settings() == {"strict": True, "prompt": "Rate the answer."}
    identity = judge_identity(judge)
    assert identity["settings"] == {"strict": True, "prompt": "Rate the answer."}
    assert identity["class"].endswith("SettingsJudge")
    assert (identity["name"], identity["model"], identity["version"]) == (
        "stub",
        "stub-model",
        "1.0",
    )
    assert identity["score_range"] == [0.0, 1.0]


def test_a_judge_can_say_what_its_settings_are() -> None:
    class ClientJudge(SettingsJudge):
        def cache_settings(self):
            return {"temperature": 0.0}

    assert judge_identity(ClientJudge(Noisy()))["settings"] == {"temperature": 0.0}


@pytest.mark.parametrize(
    ("setting", "match"),
    [
        (lambda: None, "'checker'"),
        (math.nan, "'checker'"),
        ({1, 2}, "'checker'"),
    ],
)
def test_a_judge_whose_settings_arent_json_cant_be_cached(setting, match: str) -> None:
    judge = SettingsJudge(Noisy())
    judge.checker = setting

    with pytest.raises(ValueError, match=f"can't be cached: its setting {match}"):
        CacheKeys(judge)


def test_cache_settings_must_be_a_mapping() -> None:
    class Broken(SettingsJudge):
        def cache_settings(self):
            return ["strict"]

    with pytest.raises(ValueError, match="not a mapping"):
        CacheKeys(Broken(Noisy()))


async def test_a_callable_judge_is_refused_before_any_call(cache: ResponseCache) -> None:
    noisy = Noisy()
    judge = CallableJudge(noisy, name="stub", model="stub-model", version="1.0")

    with pytest.raises(ValueError, match=r"'score_fn'.*Override cache_settings"):
        await run(judge, cache)
    assert noisy.total == 0


# --------------------------------------------------
# The runner with a cache
# --------------------------------------------------


async def test_a_rerun_is_answered_from_the_cache_without_calling_the_judge(cache) -> None:
    first_judge = Noisy()
    first, first_sink = await run(SettingsJudge(first_judge), cache)

    assert first_judge.total == 2 * TEXTS
    assert (first.cache_hits, first.cache_misses, first.cache_hit_rate) == (0, 2 * TEXTS, 0.0)
    assert not any(e.cached for e in first_sink.evaluations)
    assert len(cache) == 2 * TEXTS

    second_judge = Noisy()
    second, second_sink = await run(SettingsJudge(second_judge), cache)

    assert second_judge.total == 0
    assert (second.cache_hits, second.cache_misses, second.cache_hit_rate) == (2 * TEXTS, 0, 1.0)
    assert second.ok == second.total == 2 * TEXTS
    assert scores(second_sink) == scores(first_sink)
    by_key = {(e.target_id, e.sample_index): e for e in first_sink.evaluations}
    for e in second_sink.evaluations:
        original = by_key[(e.target_id, e.sample_index)]
        assert e.cached and e.status == OK
        assert e.model_dump(exclude={"cached"}) == original.model_dump(exclude={"cached"})


async def test_each_sample_is_its_own_call_and_comes_back_as_it_was(cache) -> None:
    # A cache keyed on the text alone would score every sample alike, and the noise floor
    # computed from them would be 0.
    _, first = await run(SettingsJudge(Noisy()), cache, samples=3)

    for target in {e.target_id for e in first.evaluations}:
        samples = sorted(
            (e.sample_index, e.score) for e in first.evaluations if e.target_id == target
        )
        assert len({score for _, score in samples}) == 3

    more_judge = Noisy()
    more, more_sink = await run(SettingsJudge(more_judge), cache, samples=5)

    # Samples 0 to 2 come from the cache; 3 and 4 are new calls.
    assert more_judge.total == 2 * TEXTS
    assert (more.cache_hits, more.cache_misses) == (3 * TEXTS, 2 * TEXTS)
    assert more.cache_hit_rate == pytest.approx(0.6)
    assert {k: v for k, v in scores(more_sink).items() if k[1] < 3} == scores(first)


async def test_refresh_calls_the_judge_every_time_and_stores_the_new_scores(tmp_path) -> None:
    path = tmp_path / "responses.sqlite3"
    with ResponseCache(path) as cache:
        await run(SettingsJudge(Noisy(base=0.5)), cache)

    fresh_judge = Noisy(base=0.7)
    with ResponseCache(path, refresh=True) as refreshing:
        fresh, fresh_sink = await run(SettingsJudge(fresh_judge), refreshing)

    assert fresh_judge.total == 2 * TEXTS
    assert (fresh.cache_hits, fresh.cache_misses, fresh.cache_hit_rate) == (0, 2 * TEXTS, 0.0)

    with ResponseCache(path) as cache:
        again, again_sink = await run(SettingsJudge(Noisy(base=0.1)), cache)
        assert len(cache) == 2 * TEXTS

    assert again.cache_hit_rate == 1.0
    assert scores(again_sink) == scores(fresh_sink)


async def test_without_a_cache_nothing_is_looked_up() -> None:
    noisy = Noisy()
    summary, sink = await run(SettingsJudge(noisy), None)

    assert noisy.total == 2 * TEXTS
    assert (summary.cache_hits, summary.cache_misses, summary.cache_hit_rate) == (0, 0, None)
    assert not any(e.cached for e in sink.evaluations)


async def test_a_different_config_doesnt_get_another_configs_scores(cache) -> None:
    await run(SettingsJudge(Noisy(), strict=False), cache)
    strict_judge = Noisy()
    summary, _ = await run(SettingsJudge(strict_judge, strict=True), cache)

    assert strict_judge.total == 2 * TEXTS
    assert summary.cache_hits == 0


async def test_failures_are_not_cached(cache) -> None:
    broken = MUTANTS[1].mutated_text
    noisy = Noisy()

    def fails_on_one(input, output, context):
        if output == broken:
            raise PermissionError("invalid API key")
        return noisy(input, output, context)

    _, first_sink = await run(SettingsJudge(fails_on_one), cache, max_consecutive_errors=None)
    assert {e.target_id for e in first_sink.evaluations if e.status == ERROR} == {
        MUTANTS[1].mutant_id
    }
    assert len(cache) == 2 * (TEXTS - 1)

    fixed = Noisy()
    second, _ = await run(SettingsJudge(fixed), cache)

    assert fixed.calls == Counter({broken: 2})
    assert (second.cache_hits, second.cache_misses, second.ok) == (2 * (TEXTS - 1), 2, 2 * TEXTS)


async def test_a_score_is_cached_before_the_sink_writes_it(cache) -> None:
    class FullDisk(MemorySink):
        async def write(self, evaluation) -> None:
            if len(self.evaluations) == 2:
                raise OSError("disk full")
            await super().write(evaluation)

    with pytest.raises(OSError, match="disk full"):
        await run(SettingsJudge(Noisy()), cache, sink=FullDisk(), max_concurrency=1)

    # The two written, and the one the sink failed on: it was paid for.
    assert len(cache) == 3


async def test_the_cache_works_with_concurrent_calls(cache) -> None:
    noisy = Noisy()
    await run(SettingsJudge(noisy), cache, samples=5, max_concurrency=4)
    again, _ = await run(SettingsJudge(Noisy()), cache, samples=5, max_concurrency=4)

    assert len(cache) == noisy.total == 5 * TEXTS
    assert again.cache_hit_rate == 1.0


async def test_cached_rows_are_stored_and_read_back(tmp_path, cache) -> None:
    await run(SettingsJudge(Noisy()), cache)
    path = tmp_path / "evaluations.jsonl"
    with JsonlSink(path) as sink:
        await run(SettingsJudge(Noisy()), cache, sink=sink)

    rows = read_evaluations(path)
    assert len(rows) == 2 * TEXTS
    assert all(row.cached for row in rows)


def test_rows_written_before_the_cache_still_read(tmp_path: Path) -> None:
    path = tmp_path / "evaluations.jsonl"
    path.write_text(
        '{"kind": "original", "target_id": "t_0", "record_id": "t_0", "sample_index": 0, '
        '"judge_name": "j", "model": "m", "version": "1", "status": "ok", "latency_ms": 1, '
        '"score": 0.5, "raw_score": 0.5}\n',
        encoding="utf-8",
        newline="\n",
    )

    (row,) = read_evaluations(path)

    assert row.cached is False


def test_run_judge_sync_passes_the_cache_on(cache) -> None:
    run_judge_sync(SettingsJudge(Noisy()), RECORDS, MUTANTS, MemorySink(), cache=cache, samples=1)
    summary = run_judge_sync(
        SettingsJudge(Noisy()), RECORDS, MUTANTS, MemorySink(), cache=cache, samples=1
    )

    assert summary.cache_hit_rate == 1.0


# --------------------------------------------------
# The file
# --------------------------------------------------


SCORE = CachedScore(score=0.75, raw_score=4.0, verdict_yes=3, n_claims=4, latency_ms=812.5)


def test_scores_survive_closing_and_reopening(tmp_path: Path) -> None:
    path = tmp_path / "responses.sqlite3"
    judge = SettingsJudge(Noisy())
    with ResponseCache(path) as cache:
        cache.put("k1", judge, SCORE)

    with ResponseCache(path) as cache:
        assert cache.get("k1") == SCORE
        assert cache.get("k2") is None
        assert len(cache) == 1


def test_a_put_replaces_what_was_stored(cache: ResponseCache) -> None:
    judge = SettingsJudge(Noisy())
    cache.put("k1", judge, SCORE)
    cache.put("k1", judge, SCORE.model_copy(update={"score": 0.25}))

    assert cache.get("k1").score == 0.25
    assert len(cache) == 1


def test_refresh_never_reads_but_still_writes(tmp_path: Path) -> None:
    path = tmp_path / "responses.sqlite3"
    judge = SettingsJudge(Noisy())
    with ResponseCache(path) as cache:
        cache.put("k1", judge, SCORE)
    with ResponseCache(path, refresh=True) as cache:
        assert cache.get("k1") is None
        cache.put("k2", judge, SCORE)
        assert len(cache) == 2


def test_an_unreadable_entry_counts_as_missing(cache: ResponseCache, caplog) -> None:
    cache.put("k1", SettingsJudge(Noisy()), SCORE)
    with sqlite3.connect(cache.path) as db:
        db.execute("UPDATE responses SET result = ? WHERE key = 'k1'", ('{"score": 7}',))

    with caplog.at_level(logging.WARNING, logger="mtlj.engine.cache"):
        assert cache.get("k1") is None
    assert "unreadable" in caplog.text


@pytest.mark.parametrize(
    "fields",
    [
        {"score": 1.5},
        {"score": math.nan},
        {"raw_score": math.inf},
        {"latency_ms": -1.0},
        {"n_claims": -1},
    ],
)
def test_a_cached_score_is_checked(fields: dict) -> None:
    with pytest.raises(ValueError):
        CachedScore.model_validate({**SCORE.model_dump(), **fields})


def test_a_file_from_another_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "responses.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")

    with pytest.raises(ValueError, match="schema version 99"):
        ResponseCache(path)


def test_the_file_records_whose_score_each_entry_is(cache: ResponseCache) -> None:
    cache.put("k1", SettingsJudge(Noisy()), SCORE)

    with sqlite3.connect(cache.path) as db:
        row = db.execute("SELECT judge_name, model, version, stored_at FROM responses").fetchone()
    assert row[:3] == ("stub", "stub-model", "1.0")
    assert row[3].endswith("+00:00")
