"""Load an operator battery from a TOML file.

A battery file lists each operator and the intensities to run it at:

    [[operators]]
    name = "verbosity_padding"
    intensities = [0.125, 0.25, 0.5, 1.0]

    [[operators]]
    name = "entity_swap"
    intensities = [0.2, 0.6, 1.0]

Names are checked against the operator registry, so any registered operator can be used
without changing this module. See ``configs/battery.example.toml``.
"""

import tomllib
from pathlib import Path
from typing import Any

from mtlj.engine.generator import BatteryItem
from mtlj.operators.registry import UnknownOperatorError, get_operator

OPERATOR_KEYS = frozenset({"name", "intensities"})


class BatteryConfigError(ValueError):
    """A battery file is missing, unreadable or invalid."""


def load_battery(path: str | Path) -> list[BatteryItem]:
    """Read the battery in ``path``, in the order its operators are listed.

    Raises:
        BatteryConfigError: the file is missing, isn't valid TOML, or doesn't describe a valid
            battery. The message names the file and the operator at fault.
    """
    path = Path(path)
    try:
        with path.open("rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        raise BatteryConfigError(f"{path}: battery file not found") from None
    except OSError as e:
        raise BatteryConfigError(f"{path}: can't read battery file: {e}") from None
    except tomllib.TOMLDecodeError as e:
        raise BatteryConfigError(f"{path}: invalid TOML: {e}") from None

    unknown = sorted(set(config) - {"operators"})
    if unknown:
        raise BatteryConfigError(f"{path}: unknown key(s) {unknown}; expected only [[operators]]")
    tables = config.get("operators")
    if not tables:
        raise BatteryConfigError(f"{path}: no [[operators]] tables")
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        raise BatteryConfigError(f"{path}: 'operators' must be a list of [[operators]] tables")

    battery: list[BatteryItem] = []
    seen: set[str] = set()
    for number, table in enumerate(tables, start=1):
        name, intensities = _parse_operator(table, f"{path}: operator {number}")
        if name in seen:
            raise BatteryConfigError(f"{path}: operator {name!r} is listed more than once")
        seen.add(name)
        try:
            operator = get_operator(name)
        except UnknownOperatorError as e:
            raise BatteryConfigError(f"{path}: operator {number}: {e}") from None
        battery.append(BatteryItem(operator, intensities))
    return battery


def _parse_operator(table: dict[str, Any], where: str) -> tuple[str, tuple[float, ...]]:
    """Check one [[operators]] table and return its name and intensities."""
    unknown = sorted(set(table) - OPERATOR_KEYS)
    if unknown:
        raise BatteryConfigError(
            f"{where}: unknown key(s) {unknown}; expected {sorted(OPERATOR_KEYS)}"
        )
    missing = sorted(OPERATOR_KEYS - set(table))
    if missing:
        raise BatteryConfigError(f"{where}: missing {', '.join(map(repr, missing))}")

    name = table["name"]
    if not isinstance(name, str):
        raise BatteryConfigError(f"{where}: 'name' must be a string, got {name!r}")
    where = f"{where} ({name})"

    raw = table["intensities"]
    if not isinstance(raw, list):
        raise BatteryConfigError(f"{where}: 'intensities' must be a list, got {raw!r}")
    if not raw:
        raise BatteryConfigError(f"{where}: 'intensities' is empty")
    intensities: list[float] = []
    for value in raw:
        # bool is an int, but `true` isn't an intensity.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise BatteryConfigError(f"{where}: intensity must be a number, got {value!r}")
        if not 0 <= value <= 1:
            raise BatteryConfigError(f"{where}: intensity must be in [0, 1], got {value}")
        if float(value) in intensities:
            raise BatteryConfigError(f"{where}: intensity {value} is listed more than once")
        intensities.append(float(value))
    return name, tuple(intensities)
