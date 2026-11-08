"""Tests for loading an operator battery from TOML."""

from pathlib import Path

import pytest

from mtlj.engine.battery_config import BatteryConfigError, load_battery
from mtlj.engine.generator import BatteryItem, default_battery
from mtlj.operators.degrading.entity_swap import EntitySwapOperator
from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator

EXAMPLE = Path(__file__).parents[2] / "configs" / "battery.example.toml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "battery.toml"
    path.write_text(text, encoding="utf-8")
    return path


def summary(battery: list[BatteryItem]) -> list[tuple[type, tuple[float, ...]]]:
    """Operator class and intensities of each item. Operator instances don't compare equal."""
    return [(type(item.operator), item.intensities) for item in battery]


def test_loads_a_valid_battery_in_order(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        [[operators]]
        name = "entity_swap"
        intensities = [0.2, 1]

        [[operators]]
        name = "verbosity_padding"
        intensities = [0.5]
        """,
    )
    battery = load_battery(path)
    assert summary(battery) == [
        (EntitySwapOperator, (0.2, 1.0)),
        (VerbosityPaddingOperator, (0.5,)),
    ]
    assert all(isinstance(i, float) for item in battery for i in item.intensities)


def test_accepts_a_string_path(tmp_path: Path) -> None:
    path = write(tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = [1.0]\n')
    assert summary(load_battery(str(path))) == [(EntitySwapOperator, (1.0,))]


def test_example_file_matches_default_battery() -> None:
    assert summary(load_battery(EXAMPLE)) == summary(default_battery())


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BatteryConfigError, match="not found"):
        load_battery(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(BatteryConfigError, match="invalid TOML"):
        load_battery(write(tmp_path, "[[operators]\nname = "))


def test_no_operators(tmp_path: Path) -> None:
    with pytest.raises(BatteryConfigError, match=r"no \[\[operators\]\]"):
        load_battery(write(tmp_path, ""))


def test_operators_must_be_tables(tmp_path: Path) -> None:
    with pytest.raises(BatteryConfigError, match="must be a list of"):
        load_battery(write(tmp_path, 'operators = ["entity_swap"]'))


def test_missing_name(tmp_path: Path) -> None:
    with pytest.raises(BatteryConfigError, match="operator 1: missing 'name'"):
        load_battery(write(tmp_path, "[[operators]]\nintensities = [0.5]\n"))


def test_missing_intensities(tmp_path: Path) -> None:
    with pytest.raises(BatteryConfigError, match="operator 1: missing 'intensities'"):
        load_battery(write(tmp_path, '[[operators]]\nname = "entity_swap"\n'))


def test_empty_intensities(tmp_path: Path) -> None:
    path = write(tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = []\n')
    with pytest.raises(BatteryConfigError, match=r"\(entity_swap\): 'intensities' is empty"):
        load_battery(path)


def test_duplicate_intensities(tmp_path: Path) -> None:
    # 1 and 1.0 are the same intensity.
    path = write(tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = [1, 0.5, 1.0]\n')
    with pytest.raises(BatteryConfigError, match=r"intensity 1\.0 is listed more than once"):
        load_battery(path)


@pytest.mark.parametrize("values", ["[1.0, 0.2]", "[0.2, 1.0, 0.6]"])
def test_intensities_must_be_listed_low_to_high(tmp_path: Path, values: str) -> None:
    # The generator keeps the first mutant at each dose, so out of order would keep the wrong one.
    path = write(tmp_path, f'[[operators]]\nname = "entity_swap"\nintensities = {values}\n')
    with pytest.raises(BatteryConfigError, match=r"\(entity_swap\): .*listed low to high"):
        load_battery(path)


@pytest.mark.parametrize("value", ["-0.1", "1.5", "nan", "inf"])
def test_intensity_outside_unit_interval(tmp_path: Path, value: str) -> None:
    path = write(tmp_path, f'[[operators]]\nname = "entity_swap"\nintensities = [{value}]\n')
    with pytest.raises(BatteryConfigError, match=r"must be in \[0, 1\]"):
        load_battery(path)


@pytest.mark.parametrize("value", ["true", '"0.5"'])
def test_intensity_must_be_a_number(tmp_path: Path, value: str) -> None:
    path = write(tmp_path, f'[[operators]]\nname = "entity_swap"\nintensities = [{value}]\n')
    with pytest.raises(BatteryConfigError, match="must be a number"):
        load_battery(path)


def test_duplicate_operator_name(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[[operators]]\nname = "entity_swap"\nintensities = [0.2]\n'
        '[[operators]]\nname = "entity_swap"\nintensities = [0.6]\n',
    )
    with pytest.raises(BatteryConfigError, match="'entity_swap' is listed more than once"):
        load_battery(path)


def test_unknown_key_in_operator(tmp_path: Path) -> None:
    path = write(tmp_path, '[[operators]]\nname = "entity_swap"\nintensities = [0.2]\nseed = 3\n')
    with pytest.raises(BatteryConfigError, match=r"operator 1: unknown key\(s\) \['seed'\]"):
        load_battery(path)


def test_unknown_top_level_key(tmp_path: Path) -> None:
    path = write(
        tmp_path, 'base_seed = 1\n[[operators]]\nname = "entity_swap"\nintensities = [0.2]\n'
    )
    with pytest.raises(BatteryConfigError, match=r"unknown key\(s\) \['base_seed'\]"):
        load_battery(path)


def test_unknown_operator_lists_available_ones(tmp_path: Path) -> None:
    path = write(tmp_path, '[[operators]]\nname = "entity_swp"\nintensities = [0.2]\n')
    with pytest.raises(BatteryConfigError) as excinfo:
        load_battery(path)
    message = str(excinfo.value)
    assert "'entity_swp'" in message
    assert "entity_swap" in message
    assert "verbosity_padding" in message


def test_name_must_be_a_string(tmp_path: Path) -> None:
    path = write(tmp_path, "[[operators]]\nname = 3\nintensities = [0.2]\n")
    with pytest.raises(BatteryConfigError, match="'name' must be a string"):
        load_battery(path)


def test_error_is_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_battery(tmp_path / "nope.toml")
