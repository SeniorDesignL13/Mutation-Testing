"""Load an audit's settings from a TOML file, so a run is reproducible and reviewable.

An audit file sets the same things as ``mtlj audit run``'s options, one key each:

    dataset = "gold"
    judge = "deepeval-faithfulness"
    battery = "battery.example.toml"
    samples = 5

Only ``dataset`` is required. Relative paths (a dataset file, a battery file, ``output``) are
read from the audit file's own directory, so a run means the same thing whichever directory
it's started from. Options given on the command line replace the file's values before anything
is checked, so an option can supply or fix a key the file lacks or gets wrong. Every value is
checked before anything is generated or scored, and a bad one fails with a message naming
where it came from: the file and its key, or the option. See ``configs/audit.example.toml``.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mtlj.engine.battery_config import load_battery
from mtlj.engine.generator import BatteryItem, default_battery
from mtlj.engine.runner import DEFAULT_MAX_CONCURRENCY, DEFAULT_SAMPLES

CORPUS_V1 = "corpus-v1"
GOLD_SET = "gold"
NAMED_DATASETS = (CORPUS_V1, GOLD_SET)
DEFAULT_BATTERY = "default"
DEFAULT_JUDGE = "deepeval-faithfulness"
BATTERY_FILE_SUFFIX = ".toml"
# What a run saves of its settings (see AuditConfig.saved_files).
SAVED_CONFIG_FILE = "config.toml"
SAVED_BATTERY_FILE = "battery.toml"


class AuditConfigError(ValueError):
    """An audit file is missing or unreadable, or the settings aren't valid."""


class AuditConfig(BaseModel):
    """The settings of one audit run. Field names match ``mtlj audit run``'s options."""

    # strict: TOML has real types, so `samples = "5"` or `samples = true` is a mistake to report.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dataset: str = Field(min_length=1)
    judge: str = Field(DEFAULT_JUDGE, min_length=1)
    model: str | None = Field(None, min_length=1)
    battery: str = Field(DEFAULT_BATTERY, min_length=1)
    samples: int = Field(DEFAULT_SAMPLES, ge=2)
    seed: int = 0
    limit: int | None = Field(None, ge=1)
    concurrency: int = Field(DEFAULT_MAX_CONCURRENCY, ge=1)
    output: str | None = Field(None, min_length=1)

    def saved_files(self, battery: Sequence[BatteryItem], model: str) -> dict[str, str]:
        """What a run saves of these settings, as file name -> text, for its own directory.

        ``battery`` is the battery the run loaded from these settings, and ``model`` the model
        its judge uses, which is the judge's default when ``self.model`` is None.

        ``config.toml`` is an audit file that reruns these settings with ``--config``. It names
        the model, so a rerun isn't changed by a judge's default changing. ``battery.toml`` holds
        the operators and intensities the run used, written from ``battery`` rather than copied
        from a file, so it's what the run used even if the file changed after it was loaded, or
        the battery came from ``default``. A dataset file is referenced by its absolute path,
        since it can be large; the records the run used are in its ``originals.jsonl``.
        ``output`` is left out: it names this run, and a rerun needs a directory of its own.
        """
        settings = self.model_copy(
            update={"output": None, "model": model, "battery": SAVED_BATTERY_FILE}
        )
        if is_dataset_file(self.dataset):
            settings = settings.model_copy(update={"dataset": str(Path(self.dataset).resolve())})
        return {
            SAVED_CONFIG_FILE: settings._to_toml(),
            SAVED_BATTERY_FILE: battery_toml(battery),
        }

    def _to_toml(self) -> str:
        """These settings as an audit file, values written as they are."""
        lines = []
        for key, value in self.model_dump(exclude_none=True).items():
            # A JSON string is a valid TOML basic string, and so is a JSON integer.
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        return "\n".join(lines) + "\n"


def battery_toml(battery: Sequence[BatteryItem]) -> str:
    """``battery`` as a battery file that ``load_battery`` reads back to the same battery."""
    tables = [
        f"[[operators]]\nname = {json.dumps(item.operator.metadata.name)}\n"
        f"intensities = {json.dumps(list(item.intensities))}\n"
        for item in battery
    ]
    return "\n".join(tables)


def is_dataset_file(dataset: str) -> bool:
    """Whether ``dataset`` is a file's path rather than the name of a built-in dataset."""
    return dataset not in NAMED_DATASETS


def is_battery_file(battery: str) -> bool:
    """Whether ``battery`` names a battery file (``*.toml``, any case) rather than operators."""
    return battery.lower().endswith(BATTERY_FILE_SUFFIX)


def read_audit_file(path: str | Path) -> dict[str, Any]:
    """The values in the audit file at ``path``, unchecked, with relative paths made relative to it.

    Raises:
        AuditConfigError: the file is missing or isn't valid TOML. The message names the file.
    """
    path = Path(path)
    try:
        with path.open("rb") as f:
            values = tomllib.load(f)
    except FileNotFoundError:
        raise AuditConfigError(f"{path}: audit file not found") from None
    except OSError as e:
        raise AuditConfigError(f"{path}: can't read audit file: {e}") from None
    except tomllib.TOMLDecodeError as e:
        raise AuditConfigError(f"{path}: invalid TOML: {e}") from None
    except UnicodeDecodeError as e:
        # tomllib decodes before it parses, so this isn't a TOMLDecodeError.
        raise AuditConfigError(f"{path}: invalid TOML: the file isn't UTF-8 ({e})") from None

    # Only non-empty strings are paths; anything else is left for validation to report.
    for key, is_path in (
        ("dataset", is_dataset_file),
        ("battery", is_battery_file),
        ("output", lambda _: True),
    ):
        value = values.get(key)
        if isinstance(value, str) and value and is_path(value):
            values[key] = _relative_to(path.parent, value)
    return values


def build_audit_config(
    file_values: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
    *,
    source: str | Path | None = None,
) -> AuditConfig:
    """Check ``file_values`` with ``overrides`` (the command line's options) on top of them.

    ``source`` is the audit file ``file_values`` came from, if any, for the error message.

    Raises:
        AuditConfigError: a value is missing or invalid. The message names every one at fault,
            each as ``<file>: <key>`` or ``--<option>``, whichever it came from.
    """
    overrides = dict(overrides or {})
    try:
        return AuditConfig.model_validate({**file_values, **overrides})
    except ValidationError as e:
        problems = [_describe(error, overrides, source) for error in e.errors()]
    if len(problems) == 1:
        raise AuditConfigError(problems[0])
    raise AuditConfigError("invalid settings:" + "".join(f"\n  - {p}" for p in problems))


def load_audit_config(path: str | Path) -> AuditConfig:
    """Read and check the audit file at ``path``, with its relative paths made relative to it.

    Raises:
        AuditConfigError: the file is missing, isn't valid TOML, or doesn't describe a valid
            audit. The message names the file and every key at fault.
    """
    return build_audit_config(read_audit_file(path), source=path)


def select_battery(battery: str) -> list[BatteryItem]:
    """The battery ``battery`` names: ``default``, a battery file, or operators of the default.

    Raises:
        ValueError: ``battery`` names operators the default battery doesn't have, or a battery
            file that can't be loaded (a ``BatteryConfigError``, whose message names the file).
    """
    if is_battery_file(battery):
        return load_battery(battery)
    items = default_battery()
    if battery == DEFAULT_BATTERY:
        return items
    by_name = {item.operator.metadata.name: item for item in items}
    names = [name.strip() for name in battery.split(",")]
    unknown = sorted(set(names) - by_name.keys())
    if unknown:
        raise ValueError(
            f"unknown operators {unknown}; choose from {sorted(by_name)}, "
            f"or give a battery file ending in {BATTERY_FILE_SUFFIX}"
        )
    return [by_name[name] for name in dict.fromkeys(names)]


def _relative_to(base: Path, value: str) -> str:
    """``value`` read from the directory ``base``; an absolute path is returned as it is.

    On Windows, a path like ``\\data\\x.jsonl`` that starts at a root but names no drive isn't
    absolute, and is read as being on ``base``'s drive.
    """
    if Path(value).is_absolute():
        return value
    return os.path.normpath(base / value)


def _describe(error: Any, overrides: Mapping[str, Any], source: str | Path | None) -> str:
    """One Pydantic error as ``where: what's wrong``, where being the option or the file's key."""
    key = ".".join(str(part) for part in error["loc"]) or "(top level)"
    from_option = key in overrides or source is None
    where = f"--{key}" if from_option else f"{source}: {key}"
    kind = error["type"]
    if kind == "extra_forbidden":
        return f"{where}: unknown key; expected one of {sorted(AuditConfig.model_fields)}"
    if kind == "missing":
        if source is None:
            return f"{where}: missing; it's required unless --config gives it"
        return f"{where}: missing; set it in the file or with --{key}"
    return f"{where}: {error['msg'].lower()}, got {error['input']!r}"
