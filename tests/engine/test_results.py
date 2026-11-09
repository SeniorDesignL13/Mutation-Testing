"""The audit result artifact, built from small hand-made runs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.engine.generator import (
    NO_CHANGE,
    OPERATOR_ERROR,
    REPEATED_DOSE,
    GenerationResult,
    SkippedMutant,
)
from mtlj.engine.results import (
    BOOTSTRAP_HEADLINE_MIN,
    RESULTS_FILE,
    SCHEMA_VERSION,
    DetectionResult,
    SpuriousResult,
    build_results,
    format_summary,
    headline_interval,
    operator_families,
    read_results,
    write_results,
)
from mtlj.engine.runner import Evaluation
from mtlj.judges.callable import CallableJudge
from mtlj.operators.base import MutationOperator, MutationResult, OperatorFamily
from mtlj.operators.degrading.entity_swap import EntitySwapOperator
from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator
from mtlj.stats.correction import correct_above_chance
from mtlj.stats.detection import MutantScores, detection_rate
from mtlj.stats.dose_response import fit_operator
from mtlj.stats.paired import paired_delta
from mtlj.stats.spurious import spurious_sensitivity
from mtlj.stats.variance import Condition, condition_variances

SWAP = EntitySwapOperator.metadata.name
PAD = VerbosityPaddingOperator.metadata.name
JUDGE = CallableJudge(lambda *_: 1.0, name="stub", model="m", version="1")
ORIGINAL_SCORES = [0.9, 0.92, 0.88, 0.91, 0.89]
BATTERY = {SWAP: [0.2, 1.0], PAD: [0.5]}
FAMILIES = {SWAP: OperatorFamily.DEGRADING, PAD: OperatorFamily.PRESERVING}


def record(i: int) -> DatasetRecord:
    return DatasetRecord(
        record_id=f"r{i}",
        source_dataset="test",
        question="q",
        context=[{"passage_id": "p", "text": "t"}],
        answer=f"Answer {i}.",
    )


def mutant(source: DatasetRecord, operator: type[MutationOperator], intensity: float) -> Mutant:
    text = f"{source.answer} {operator.metadata.name} {intensity}"
    result = MutationResult(source.answer, text, True, {})
    return Mutant.from_result(source, operator(), result, intensity, seed=1)


def evaluation(
    target_id: str, record_id: str, sample: int, score: float | None, **kw
) -> Evaluation:
    fields = {
        "kind": "mutant" if target_id != record_id else "original",
        "target_id": target_id,
        "record_id": record_id,
        "sample_index": sample,
        "judge_name": "stub",
        "model": "m",
        "version": "1",
        "status": "ok" if score is not None else "error",
        "latency_ms": 1.0,
        "score": score,
        "raw_score": score,
    }
    return Evaluation(**{**fields, **kw})


def scored(target_id: str, record_id: str, scores: list[float]) -> list[Evaluation]:
    return [evaluation(target_id, record_id, i, s) for i, s in enumerate(scores)]


def make_run(
    swap_scores: float = 0.2, pad_scores: float = 0.9, n_records: int = 4
) -> tuple[GenerationResult, list[Evaluation]]:
    """Four records, two swap intensities and one padding intensity."""
    records = [record(i) for i in range(n_records)]
    mutants = [
        *(mutant(r, EntitySwapOperator, 0.2) for r in records),
        *(mutant(r, EntitySwapOperator, 1.0) for r in records),
        *(mutant(r, VerbosityPaddingOperator, 0.5) for r in records),
    ]
    generation = GenerationResult(
        records, mutants, [], base_seed=7, battery=BATTERY, families=FAMILIES
    )
    rows = [e for r in records for e in scored(r.record_id, r.record_id, ORIGINAL_SCORES)]
    for m in mutants:
        base = swap_scores if m.operator_name == SWAP else pad_scores
        rows += scored(m.mutant_id, m.record_id, [base + o for o in (0, 0.01, -0.01, 0.02, -0.02)])
    return generation, rows


def build(generation, rows, **kwargs):
    return build_results(generation, rows, judge=JUDGE, dataset="test", samples=5, **kwargs)


def operator(results, name):
    return next(o for o in results.operators if o.operator == name)


def test_run_metadata_records_how_the_run_was_made():
    results = build(*make_run())

    run = results.run
    assert results.schema_version == SCHEMA_VERSION
    assert (run.judge.name, run.judge.model, run.judge.version) == ("stub", "m", "1")
    assert (run.dataset, run.n_records) == ("test", 4)
    assert (run.samples, run.alpha, run.ci_level) == (5, 0.05, 0.95)
    assert (run.base_seed, run.battery, run.status) == (7, BATTERY, "complete")
    assert (run.scorings.total, run.scorings.ok, run.scorings.errors) == (80, 80, 0)


def test_rates_match_the_stats_modules():
    generation, rows = make_run()
    results = build(generation, rows)

    by_target: dict[str, list[float]] = {}
    for row in rows:
        by_target.setdefault(row.target_id, []).append(row.score)
    originals = {r.record_id: by_target[r.record_id] for r in generation.originals}

    def group(name):
        return [
            MutantScores(m.record_id, by_target[m.mutant_id])
            for m in generation.mutants
            if m.operator_name == name
        ]

    expected = detection_rate(originals, group(SWAP))
    overall = operator(results, SWAP).overall
    assert isinstance(overall, DetectionResult)
    assert overall.rate == expected.rate
    assert overall.wilson_ci == expected.ci
    assert overall.detected == expected.detected
    assert overall.tau == expected.tau
    assert overall.paired_delta.mean == paired_delta(expected.outcomes).mean

    spurious = spurious_sensitivity(originals, group(PAD))
    pad = operator(results, PAD).overall
    assert isinstance(pad, SpuriousResult)
    assert pad.rate == spurious.rate
    assert pad.p_value == spurious.p_value
    assert pad.above_chance == spurious.above_chance
    assert results.noise.sigma == expected.sigma


def test_families_get_their_own_metric():
    results = build(*make_run())

    assert operator(results, SWAP).family is OperatorFamily.DEGRADING
    assert operator(results, SWAP).overall.metric == "detection_rate"
    assert operator(results, PAD).family is OperatorFamily.PRESERVING
    assert operator(results, PAD).overall.metric == "spurious_sensitivity"


def test_operator_is_reported_pooled_and_at_each_intensity():
    swap = operator(build(*make_run()), SWAP)

    assert [i.intensity for i in swap.by_intensity] == [0.2, 1.0]
    assert [i.result.testable for i in swap.by_intensity] == [4, 4]
    assert swap.overall.testable == 8


def test_variance_matches_the_stats_module():
    generation, rows = make_run()
    results = build(generation, rows)

    by_target: dict[str, list[float]] = {}
    for row in rows:
        by_target.setdefault(row.target_id, []).append(row.score)
    originals = {r.record_id: by_target[r.record_id] for r in generation.originals}
    conditions: dict[Condition, dict[str, list[float]]] = {}
    for m in generation.mutants:
        condition = Condition(m.operator_name, m.intensity)
        conditions.setdefault(condition, {})[m.mutant_id] = by_target[m.mutant_id]

    expected = condition_variances(originals, conditions).to_dict()
    assert results.variance == json.loads(json.dumps(expected))


def test_unchanged_mutants_count_as_no_change():
    generation, rows = make_run()
    generation.skipped.append(SkippedMutant("r0", SWAP, 0.2, 1, NO_CHANGE))

    swap = operator(build(generation, rows), SWAP)

    assert swap.overall.excluded["no_change"] == 1
    assert swap.by_intensity[0].result.excluded["no_change"] == 1
    assert swap.by_intensity[1].result.excluded["no_change"] == 0


def test_other_skips_are_not_counted_as_no_change():
    generation, rows = make_run()
    generation.skipped.append(SkippedMutant("r0", SWAP, 0.2, 1, "repeated_dose"))

    assert operator(build(generation, rows), SWAP).overall.excluded["no_change"] == 0


def test_failed_scorings_are_counted_and_lower_a_texts_sample_count():
    generation, rows = make_run()
    target = generation.mutants[0]
    rows = [r for r in rows if r.target_id != target.mutant_id]
    rows.append(evaluation(target.mutant_id, target.record_id, 0, None, error_type="Timeout"))

    results = build(generation, rows)

    assert results.run.scorings.errors == 1
    assert results.run.scorings.errors_by_type == {"Timeout": 1}
    assert results.run.status == "complete"
    assert operator(results, SWAP).by_intensity[0].result.excluded["no_scores"] == 1


def test_operator_with_no_mutants_is_still_reported():
    generation, rows = make_run()
    generation.battery = {**BATTERY, SWAP: [0.2, 0.6, 1.0]}

    swap = operator(build(generation, rows), SWAP)

    empty = swap.by_intensity[1]
    assert empty.intensity == 0.6
    assert (empty.result.rate, empty.result.wilson_ci, empty.result.testable) == (None, None, 0)
    assert (empty.result.bootstrap_ci, empty.result.headline_interval) == (None, None)


def test_operator_outside_the_built_in_list_is_reported():
    generation, rows = make_run()
    generation.battery = {**BATTERY, "custom": [1.0]}
    generation.families = {**FAMILIES, "custom": OperatorFamily.PRESERVING}

    assert operator(build(generation, rows), "custom").overall.testable == 0


def test_operator_crashes_are_counted_and_shown():
    generation, rows = make_run()
    generation.skipped.append(SkippedMutant("r0", SWAP, 0.2, 1, OPERATOR_ERROR))

    results = build(generation, rows)

    assert operator(results, SWAP).operator_errors == 1
    assert operator(results, PAD).operator_errors == 0
    assert "1 operator errors" in format_summary(results)


def test_binomial_test_is_left_out_when_pooling_intensities():
    records = [record(i) for i in range(4)]
    mutants = [mutant(r, VerbosityPaddingOperator, i) for r in records for i in (0.5, 1.0)]
    generation = GenerationResult(
        records, mutants, [], battery={PAD: [0.5, 1.0]}, families=FAMILIES
    )
    rows = [e for r in records for e in scored(r.record_id, r.record_id, ORIGINAL_SCORES)]
    for m in mutants:
        rows += scored(m.mutant_id, m.record_id, [0.2, 0.21, 0.19, 0.2, 0.2])

    results = build(generation, rows)

    pad = operator(results, PAD)
    assert (pad.overall.p_value, pad.overall.above_chance) == (None, None)
    assert pad.overall.rate == 1.0
    assert all(i.result.above_chance is True for i in pad.by_intensity)
    assert all(i.result.p_value is not None for i in pad.by_intensity)
    overall_line = next(
        line for line in format_summary(results).splitlines() if line.startswith(f"  {PAD}:")
    )
    assert "above chance" not in overall_line


def test_nothing_testable_means_the_test_was_not_run():
    generation, rows = make_run()
    generation.battery = {**BATTERY, PAD: [0.5]}
    generation.mutants = [m for m in generation.mutants if m.operator_name != PAD]

    pad = operator(build(generation, rows), PAD).by_intensity[0].result

    assert (pad.rate, pad.p_value, pad.above_chance) == (None, None, None)


def test_family_can_come_from_the_mutants():
    generation, rows = make_run()
    generation.families = {}

    assert operator(build(generation, rows), SWAP).family is OperatorFamily.DEGRADING


def test_operator_with_no_family_anywhere_is_rejected():
    generation, _ = make_run()
    generation.families = {}
    generation.battery = {**BATTERY, "custom": [1.0]}

    with pytest.raises(ValueError, match="no family recorded"):
        operator_families(generation)


def test_single_intensity_keeps_the_binomial_test():
    pad = operator(build(*make_run()), PAD)

    assert pad.overall.p_value is not None
    assert pad.overall.above_chance is False


def test_intensity_with_no_mutants_is_in_the_variance_report():
    generation, rows = make_run()
    generation.battery = {**BATTERY, SWAP: [0.2, 0.6, 1.0]}

    conditions = build(generation, rows).variance["conditions"]

    empty = [c for c in conditions if c["operator"] == SWAP and c["intensity"] == 0.6]
    assert [c["estimate"] for c in empty] == [None]


def test_aborted_run_reports_why_and_no_rates():
    generation, rows = make_run()

    results = build(generation, rows[:30], aborted="20 judge calls in a row failed")

    assert results.run.status == "aborted"
    assert results.run.reason == "20 judge calls in a row failed"
    assert results.run.scorings.total == 30
    assert (results.noise, results.operators, results.variance) == (None, [], None)
    assert results.correction is None


def test_single_sample_run_is_insufficient_data_not_a_crash():
    generation, rows = make_run()

    results = build(generation, [r for r in rows if r.sample_index == 0])

    assert results.run.status == "insufficient_data"
    assert "noise" in results.run.reason
    assert results.operators == []


def test_run_with_no_evaluations_is_insufficient_data():
    generation, _ = make_run()

    assert build(generation, []).run.status == "insufficient_data"


def test_judge_with_no_noise_is_reported():
    generation, rows = make_run()
    rows = [r.model_copy(update={"score": 0.9, "raw_score": 0.9}) for r in rows]

    results = build(generation, rows)

    assert results.noise.sigma == 0.0
    assert operator(results, SWAP).overall.effect_size is None


def test_evaluations_from_another_judge_are_rejected():
    generation, rows = make_run()
    rows[3] = rows[3].model_copy(update={"model": "other"})

    with pytest.raises(ValueError, match="another judge"):
        build(generation, rows)


def test_results_survive_a_round_trip(tmp_path: Path):
    results = build(*make_run())

    path = write_results(results, tmp_path)

    assert path == tmp_path / RESULTS_FILE
    assert read_results(path) == results
    assert read_results(tmp_path) == results


def test_no_partial_file_is_left_behind(tmp_path: Path):
    write_results(build(*make_run()), tmp_path)

    assert [path.name for path in tmp_path.iterdir()] == [RESULTS_FILE]


def test_rebuilding_a_run_writes_identical_bytes(tmp_path: Path):
    generation, rows = make_run()
    first = write_results(build(generation, rows), tmp_path).read_bytes()

    second = write_results(build(generation, list(reversed(rows))), tmp_path).read_bytes()

    assert first == second


def test_file_is_strict_json_with_the_documented_top_level(tmp_path: Path):
    generation, rows = make_run()
    rows = [r.model_copy(update={"score": 0.9, "raw_score": 0.9}) for r in rows]

    data = json.loads(write_results(build(generation, rows), tmp_path).read_text("utf-8"))

    assert sorted(data) == [
        "correction",
        "noise",
        "operators",
        "run",
        "schema_version",
        "variance",
    ]


def test_unknown_schema_version_is_rejected(tmp_path: Path):
    path = write_results(build(*make_run()), tmp_path)
    data = json.loads(path.read_text("utf-8"))
    data["schema_version"] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(data), "utf-8")

    with pytest.raises(ValueError, match="schema version"):
        read_results(path)


@pytest.mark.parametrize("text", ["[]", "null", "3"])
def test_valid_json_that_is_not_results_is_rejected(tmp_path: Path, text: str):
    path = tmp_path / RESULTS_FILE
    path.write_text(text, "utf-8")

    with pytest.raises(ValueError, match="schema version"):
        read_results(path)


def test_summary_shows_each_metric_and_the_counts_behind_it():
    summary = format_summary(build(*make_run()))

    assert "Judge: stub (m, version 1)" in summary
    assert "Dataset: test (4 records)" in summary
    assert "Status: complete" in summary
    assert "Detection rate (degrading operators)" in summary
    assert f"{SWAP}: 100.0%" in summary
    assert "8/8 detected" in summary
    assert "intensity 0.2: 100.0%" in summary
    assert "Spurious sensitivity (preserving operators, read against 5%)" in summary
    assert "0/4 flagged" in summary


def test_summary_says_why_a_run_has_no_rates():
    generation, rows = make_run()

    summary = format_summary(build(generation, rows, aborted="judge is down"))

    assert "Status: aborted (judge is down)" in summary
    assert "Detection rate" not in summary


def test_summary_of_an_empty_rate_reads_na():
    generation, rows = make_run()
    generation.battery = {**BATTERY, SWAP: [0.2, 0.6, 1.0]}

    assert "intensity 0.6: n/a" in format_summary(build(generation, rows))


def raw_rates(generation, rows):
    """Each (operator, intensity)'s rate, straight from the stats modules."""
    by_target: dict[str, list[float]] = {}
    for row in rows:
        by_target.setdefault(row.target_id, []).append(row.score)
    originals = {r.record_id: by_target[r.record_id] for r in generation.originals}
    rates = {}
    for name, intensities in generation.battery.items():
        metric = (
            detection_rate if FAMILIES[name] is OperatorFamily.DEGRADING else spurious_sensitivity
        )
        for intensity in intensities:
            group = [
                MutantScores(m.record_id, by_target[m.mutant_id])
                for m in generation.mutants
                if (m.operator_name, m.intensity) == (name, intensity)
            ]
            rates[(name, intensity)] = metric(originals, group)
    return rates


def test_bootstrap_interval_matches_the_stats_module():
    generation, rows = make_run()
    expected = raw_rates(generation, rows)[(PAD, 0.5)].bootstrap

    bootstrap = operator(build(generation, rows), PAD).by_intensity[0].result.bootstrap_ci

    assert bootstrap.ci == expected.ci
    assert (bootstrap.level, bootstrap.n_resamples, bootstrap.n_valid, bootstrap.seed) == (
        expected.level,
        expected.n_resamples,
        expected.n_valid,
        expected.seed,
    )


def test_pooled_figure_has_a_bootstrap_interval():
    swap = operator(build(*make_run()), SWAP)

    assert swap.overall.bootstrap_ci is not None
    assert swap.overall.bootstrap_ci.n_valid > 0


@pytest.mark.parametrize(
    ("testable", "expected"),
    [
        (0, None),
        (1, "wilson"),
        (BOOTSTRAP_HEADLINE_MIN - 1, "wilson"),
        (BOOTSTRAP_HEADLINE_MIN, "bootstrap"),
    ],
)
def test_headline_interval_switches_to_the_bootstrap_at_100(testable, expected):
    assert headline_interval(testable) == expected


def test_small_result_headlines_wilson():
    swap = operator(build(*make_run()), SWAP).by_intensity[0].result

    assert swap.headline_interval == "wilson"
    assert swap.headline_ci == swap.wilson_ci
    assert "(Wilson)" in format_summary(build(*make_run()))


def test_large_result_headlines_the_bootstrap():
    results = build(*make_run(n_records=BOOTSTRAP_HEADLINE_MIN))

    swap = operator(results, SWAP).by_intensity[0].result
    assert swap.testable == BOOTSTRAP_HEADLINE_MIN
    assert swap.headline_interval == "bootstrap"
    assert swap.headline_ci == swap.bootstrap_ci.ci
    assert "(bootstrap)" in format_summary(results)


def test_detection_rate_has_the_binomial_test_except_when_pooled():
    generation, rows = make_run()
    expected = raw_rates(generation, rows)[(SWAP, 0.2)]

    swap = operator(build(generation, rows), SWAP)

    assert (swap.overall.p_value, swap.overall.above_chance) == (None, None)
    assert (swap.overall.p_adjusted, swap.overall.above_chance_corrected) == (None, None)
    first = swap.by_intensity[0].result
    assert first.p_value == expected.p_value
    assert first.above_chance is expected.above_chance is True


def test_correction_covers_every_operator_at_every_intensity():
    generation, rows = make_run()
    expected = correct_above_chance(raw_rates(generation, rows))

    results = build(generation, rows)

    assert results.correction.method == expected.method
    assert results.correction.q == 0.05
    assert results.correction.n_tests == expected.n_tests == 3
    for o in results.operators:
        for i in o.by_intensity:
            test = expected.tests[(o.operator, i.intensity)]
            assert i.result.p_adjusted == test.adjusted
            assert i.result.above_chance_corrected is test.significant_corrected


def test_single_intensity_overall_carries_its_corrected_test():
    pad = operator(build(*make_run()), PAD)

    assert pad.overall == pad.by_intensity[0].result
    assert pad.overall.p_adjusted is not None


def test_untestable_intensity_is_left_out_of_the_correction():
    generation, rows = make_run()
    generation.battery = {**BATTERY, SWAP: [0.2, 0.6, 1.0]}

    results = build(generation, rows)

    assert results.correction.n_tests == 3
    empty = operator(results, SWAP).by_intensity[1].result
    assert (empty.p_adjusted, empty.above_chance_corrected) == (None, None)


def test_summary_says_when_only_the_uncorrected_test_is_above_chance():
    results = build(*make_run())
    pad = operator(results, PAD)
    marginal = pad.overall.model_copy(
        update={"above_chance": True, "above_chance_corrected": False}
    )
    operators = [
        o.model_copy(update={"overall": marginal}) if o.operator == PAD else o
        for o in results.operators
    ]

    summary = format_summary(results.model_copy(update={"operators": operators}))

    assert "above chance before correction only" in summary
    assert "Above chance: corrected across 3 tests (benjamini-hochberg, q = 0.05)" in summary


def test_dose_response_is_fitted_for_operators_with_several_intensities():
    generation, rows = make_run()
    rates = raw_rates(generation, rows)
    expected = fit_operator({i: rates[(SWAP, i)] for i in BATTERY[SWAP]})

    results = build(generation, rows)

    curve = operator(results, SWAP).dose_response
    assert operator(results, PAD).dose_response is None
    assert curve.onset == expected.onset
    assert curve.onset_ci == expected.onset_ci
    assert (curve.responds, curve.in_range, curve.slope) == (
        expected.responds,
        expected.in_range,
        expected.slope,
    )
    assert [(p.intensity, p.hits, p.testable) for p in curve.points] == [(0.2, 4, 4), (1.0, 4, 4)]
    assert [p.fitted_rate for p in curve.points] == [expected.rate(0.2), expected.rate(1.0)]


def test_judge_that_never_responds_has_an_open_onset_interval(tmp_path: Path):
    results = build(*make_run(swap_scores=0.9))

    curve = operator(results, SWAP).dose_response
    assert curve.onset is None
    assert curve.onset_ci[1] is None
    assert curve.responds is False
    write_results(results, tmp_path)
    assert read_results(tmp_path) == results
    assert "dose-response: no response at the tested intensities" in format_summary(results)


def test_intensity_zero_is_left_out_of_the_curve():
    generation, rows = make_run()
    zero = [mutant(r, EntitySwapOperator, 0.0) for r in generation.originals]
    generation.mutants += zero
    generation.battery = {**BATTERY, SWAP: [0.0, 0.2, 1.0]}
    for m in zero:
        rows += scored(m.mutant_id, m.record_id, ORIGINAL_SCORES)

    swap = operator(build(generation, rows), SWAP)

    assert swap.by_intensity[0].result.testable == 4
    assert [p.intensity for p in swap.dose_response.points] == [0.2, 1.0]


def test_summary_shows_the_dose_response_line():
    assert "    dose-response: onset " in format_summary(build(*make_run()))


def test_summary_lists_exclusions_by_name():
    generation, rows = make_run()
    generation.skipped.append(SkippedMutant("r0", SWAP, 0.2, 1, NO_CHANGE))

    summary = format_summary(build(generation, rows))

    assert "excluded no_change 1" in summary
    assert "{" not in summary


def test_fields_added_by_a_newer_version_are_ignored(tmp_path: Path):
    results = build(*make_run())
    path = write_results(results, tmp_path)
    data = json.loads(path.read_text("utf-8"))
    data["added_later"] = {"anything": 1}
    data["run"]["added_later"] = True
    data["operators"][0]["overall"]["added_later"] = [1, 2]
    path.write_text(json.dumps(data), "utf-8")

    assert read_results(path) == results


def test_a_missing_field_is_still_rejected(tmp_path: Path):
    path = write_results(build(*make_run()), tmp_path)
    data = json.loads(path.read_text("utf-8"))
    del data["operators"][0]["overall"]["wilson_ci"]
    path.write_text(json.dumps(data), "utf-8")

    with pytest.raises(ValueError, match="wilson_ci"):
        read_results(path)


def test_no_bootstrap_interval_means_wilson_is_the_headline():
    assert headline_interval(BOOTSTRAP_HEADLINE_MIN, has_bootstrap=False) == "wilson"
    assert headline_interval(0, has_bootstrap=False) is None


def test_headline_ci_falls_back_to_wilson_when_the_bootstrap_has_no_interval():
    result = operator(build(*make_run()), SWAP).by_intensity[0].result
    empty = result.bootstrap_ci.model_copy(update={"ci": None, "n_valid": 0})

    broken = result.model_copy(update={"headline_interval": "bootstrap", "bootstrap_ci": empty})

    assert broken.headline_ci == result.wilson_ci


def test_repeated_doses_are_counted_at_their_intensity_and_shown():
    generation, rows = make_run()
    generation.skipped.append(SkippedMutant("r0", SWAP, 1.0, 1, REPEATED_DOSE))

    results = build(generation, rows)

    swap = operator(results, SWAP)
    assert [i.repeated_doses for i in swap.by_intensity] == [0, 1]
    assert swap.repeated_doses == 1
    assert operator(results, PAD).repeated_doses == 0
    assert swap.overall.excluded == {"no_change": 0, "no_scores": 0, "no_headroom": 0}
    summary = format_summary(results)
    assert "intensity 1: " in summary
    assert "1 repeated doses left out" in next(
        line for line in summary.splitlines() if line.startswith("    intensity 1:")
    )
