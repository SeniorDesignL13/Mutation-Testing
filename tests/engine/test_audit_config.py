import tomllib
from pathlib import Path

import pytest

from mtlj.engine.audit_config import (
    AuditConfig,
    AuditConfigError,
    build_audit_config,
    is_battery_file,
    load_audit_config,
    read_audit_file,
    select_battery,
)
from mtlj.engine.battery_config import BatteryConfigError, load_battery
from mtlj.engine.generator import default_battery
from mtlj.engine.runner import DEFAULT_MAX_CONCURRENCY, DEFAULT_SAMPLES

CONFIGS = Path(__file__).parents[2] / "configs"
EXAMPLE = CONFIGS / "audit.example.toml"


def write(tmp_path: Path, text: str, name: str = "audit.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def summary(battery):
    return [(type(item.operator), item.intensities) for item in battery]


# --------------------------------------------------
# What a valid file gives
# --------------------------------------------------


def test_example_config_loads_with_five_samples_and_the_example_battery() -> None:
    config = load_audit_config(EXAMPLE)

    assert config.dataset == "gold"
    assert config.judge == "deepeval-faithfulness"
    assert config.samples == 5
    assert Path(config.battery) == CONFIGS / "battery.example.toml"
    assert summary(select_battery(config.battery)) == summary(default_battery())


def test_only_dataset_is_required_and_the_rest_default_to_the_cli_defaults(
    tmp_path: Path,
) -> None:
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\n'))

    assert config == AuditConfig(
        dataset="gold",
        judge="deepeval-faithfulness",
        model=None,
        battery="default",
        samples=DEFAULT_SAMPLES,
        seed=0,
        limit=None,
        concurrency=DEFAULT_MAX_CONCURRENCY,
        output=None,
    )
    assert DEFAULT_SAMPLES == 5


def test_relative_paths_are_read_from_the_config_files_directory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    path = write(
        tmp_path / "sub",
        'dataset = "../data.jsonl"\nbattery = "b.toml"\noutput = "runs/x"\n',
    )

    config = load_audit_config(path)

    assert Path(config.dataset) == tmp_path / "data.jsonl"
    assert Path(config.battery) == tmp_path / "sub" / "b.toml"
    assert Path(config.output) == tmp_path / "sub" / "runs" / "x"


def test_absolute_paths_and_named_datasets_are_left_alone(tmp_path: Path) -> None:
    battery = tmp_path / "elsewhere" / "b.toml"
    path = write(tmp_path, f"dataset = \"corpus-v1\"\nbattery = '{battery}'\n")

    config = load_audit_config(path)

    assert config.dataset == "corpus-v1"
    assert Path(config.battery) == battery


def test_a_filter_of_default_operators_is_not_a_path(tmp_path: Path) -> None:
    path = write(tmp_path, 'dataset = "gold"\nbattery = "entity_swap,verbosity_padding"\n')

    config = load_audit_config(path)

    assert config.battery == "entity_swap,verbosity_padding"
    names = [i.operator.metadata.name for i in select_battery(config.battery)]
    assert names == ["entity_swap", "verbosity_padding"]


def save(config: AuditConfig, directory: Path, model: str = "judge-default") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    battery = select_battery(config.battery)
    for name, text in config.saved_files(battery, config.model or model).items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory / "config.toml"


def test_saved_settings_load_back_the_same_from_anywhere(tmp_path: Path) -> None:
    config = load_audit_config(EXAMPLE).model_copy(
        update={"model": 'odd "name" é', "limit": 3, "output": "runs/x"}
    )

    again = load_audit_config(save(config, tmp_path / "run"))

    # output names the run that saved the file, so a rerun gets a directory of its own.
    assert again.model_copy(update={"battery": config.battery}) == config.model_copy(
        update={"output": None}
    )
    assert "output" not in tomllib.loads((tmp_path / "run" / "config.toml").read_text("utf-8"))


def test_a_saved_battery_is_a_copy_so_later_edits_dont_change_a_rerun(tmp_path: Path) -> None:
    battery = write(
        tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = [1.0]\n', "b.toml"
    )
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\nbattery = "b.toml"\n'))
    saved = save(config, tmp_path / "run")

    battery.write_text('[[operators]]\nname = "entity_swap"\nintensities = [0.2]\n', "utf-8")
    again = load_audit_config(saved)

    assert Path(again.battery) == tmp_path / "run" / "battery.toml"
    assert [item.intensities for item in select_battery(again.battery)] == [(1.0,)]


def test_a_saved_dataset_file_is_absolute_so_the_run_directory_can_move(tmp_path: Path) -> None:
    config = load_audit_config(write(tmp_path, 'dataset = "data.jsonl"\n'))
    saved = save(config, tmp_path / "run")

    moved = tmp_path / "moved"
    (tmp_path / "run").rename(moved)

    assert Path(load_audit_config(moved / saved.name).dataset) == (tmp_path / "data.jsonl")


# --------------------------------------------------
# Options on top of a file
# --------------------------------------------------


def test_an_option_supplies_a_key_the_file_leaves_out(tmp_path: Path) -> None:
    values = read_audit_file(write(tmp_path, "samples = 3\n"))

    config = build_audit_config(values, {"dataset": "gold"}, source="audit.toml")

    assert (config.dataset, config.samples) == ("gold", 3)


def test_an_option_replaces_a_bad_value_in_the_file(tmp_path: Path) -> None:
    values = read_audit_file(write(tmp_path, 'dataset = "gold"\nsamples = 1\n'))

    assert build_audit_config(values, {"samples": 3}, source="audit.toml").samples == 3


def test_a_bad_option_is_named_as_the_option(tmp_path: Path) -> None:
    values = read_audit_file(write(tmp_path, 'dataset = "gold"\n'))

    with pytest.raises(AuditConfigError, match=r"^--model: string should have at least 1"):
        build_audit_config(values, {"model": ""}, source="audit.toml")


def test_without_a_file_a_missing_dataset_names_the_option() -> None:
    with pytest.raises(AuditConfigError, match=r"^--dataset: missing; .*unless --config"):
        build_audit_config({}, {"samples": 3})


def test_a_file_missing_dataset_says_where_to_set_it(tmp_path: Path) -> None:
    with pytest.raises(AuditConfigError, match=r"audit\.toml: dataset: missing; set it in the"):
        load_audit_config(write(tmp_path, "samples = 3\n"))


def test_an_empty_path_is_reported_not_read_as_the_files_directory(tmp_path: Path) -> None:
    with pytest.raises(AuditConfigError) as raised:
        load_audit_config(write(tmp_path, 'dataset = ""\noutput = ""\n'))

    assert "- " in str(raised.value)
    for key in ("dataset", "output"):
        assert f"{key}: string should have at least 1 character" in str(raised.value)


@pytest.mark.parametrize(
    ("battery", "is_file"),
    [
        ("b.toml", True),
        ("configs/Battery.TOML", True),
        ("x.Toml", True),
        ("default", False),
        ("entity_swap,verbosity_padding", False),
    ],
)
def test_a_battery_file_is_any_toml_whatever_its_case(battery: str, is_file: bool) -> None:
    assert is_battery_file(battery) is is_file


def test_an_upper_case_battery_file_loads(tmp_path: Path) -> None:
    write(tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = [1.0]\n', "B.TOML")

    config = load_audit_config(write(tmp_path, 'dataset = "gold"\nbattery = "B.TOML"\n'))

    assert [i.operator.metadata.name for i in select_battery(config.battery)] == ["entity_swap"]


# --------------------------------------------------
# Invalid files fail with a message naming the file and the key
# --------------------------------------------------


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AuditConfigError, match=r"nope\.toml: audit file not found"):
        load_audit_config(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(AuditConfigError, match=r"audit\.toml: invalid TOML"):
        load_audit_config(write(tmp_path, "dataset = "))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", r"dataset: missing; set it in the file or with --dataset"),
        ('dataset = ""', r"dataset: string should have at least 1 character"),
        ('dataset = "gold"\nsamples = 1', r"samples: .*greater than or equal to 2, got 1"),
        ('dataset = "gold"\nsamples = "5"', r"samples: input should be a valid integer"),
        ('dataset = "gold"\nsamples = 5.0', r"samples: input should be a valid integer"),
        ('dataset = "gold"\nsamples = true', r"samples: input should be a valid integer"),
        ('dataset = "gold"\nlimit = 0', r"limit: .*greater than or equal to 1"),
        ('dataset = "gold"\nconcurrency = 0', r"concurrency: .*greater than or equal to 1"),
        ('dataset = "gold"\nseed = "zero"', r"seed: input should be a valid integer"),
        ("dataset = 3", r"dataset: input should be a valid string"),
        ('dataset = "gold"\nsample_count = 5', r"sample_count: unknown key; expected one of"),
        ('dataset = "gold"\n[judge]\nname = "x"', r"judge: input should be a valid string"),
    ],
)
def test_invalid_values_name_the_key(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(AuditConfigError, match=r"audit\.toml: " + message):
        load_audit_config(write(tmp_path, text))


def test_every_bad_key_is_reported_at_once(tmp_path: Path) -> None:
    with pytest.raises(AuditConfigError) as raised:
        load_audit_config(write(tmp_path, "samples = 1\ncolour = 'red'\n"))

    message = str(raised.value)
    for key in ("dataset", "samples", "colour"):
        assert f"audit.toml: {key}:" in message


def test_a_bad_battery_file_reports_the_battery_files_own_error(tmp_path: Path) -> None:
    write(tmp_path, '[[operators]]\nname = "no_such_operator"\nintensities = [1.0]\n', "b.toml")
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\nbattery = "b.toml"\n'))

    with pytest.raises(BatteryConfigError, match=r"b\.toml: operator 1: .*no_such_operator"):
        select_battery(config.battery)


def test_a_missing_battery_file_is_reported(tmp_path: Path) -> None:
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\nbattery = "gone.toml"\n'))

    with pytest.raises(BatteryConfigError, match=r"gone\.toml: battery file not found"):
        select_battery(config.battery)


def test_unknown_operators_in_a_filter_are_reported() -> None:
    with pytest.raises(ValueError, match=r"unknown operators \['nope'\]"):
        select_battery("entity_swap,nope")


def test_a_battery_file_loads_like_load_battery() -> None:
    path = str(CONFIGS / "battery.example.toml")

    assert summary(select_battery(path)) == summary(load_battery(path))


def test_the_saved_battery_is_the_one_loaded_even_from_default(tmp_path: Path) -> None:
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\n'))

    again = load_audit_config(save(config, tmp_path / "run"))

    assert Path(again.battery) == tmp_path / "run" / "battery.toml"
    assert summary(select_battery(again.battery)) == summary(default_battery())


def test_the_saved_battery_is_what_was_loaded_not_the_file_as_it_is_later(
    tmp_path: Path,
) -> None:
    battery = write(
        tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = [1.0]\n', "b.toml"
    )
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\nbattery = "b.toml"\n'))
    loaded = select_battery(config.battery)

    # The file changes after the run loaded it, but before it saved its settings.
    battery.write_text('[[operators]]\nname = "entity_swap"\nintensities = [0.2]\n', "utf-8")
    saved = config.saved_files(loaded, "m")["battery.toml"]

    assert tomllib.loads(saved) == {"operators": [{"name": "entity_swap", "intensities": [1.0]}]}


def test_the_saved_config_names_the_model_the_judge_used(tmp_path: Path) -> None:
    config = load_audit_config(write(tmp_path, 'dataset = "gold"\n'))
    assert config.model is None

    again = load_audit_config(save(config, tmp_path / "run", model="gpt-4o-mini"))

    assert again.model == "gpt-4o-mini"


def test_a_file_that_isnt_utf8_is_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "audit.toml"
    path.write_bytes('dataset = "caf\xe9.jsonl"\n'.encode("latin-1"))

    with pytest.raises(AuditConfigError, match=r"audit\.toml: invalid TOML: the file isn't UTF-8"):
        load_audit_config(path)
