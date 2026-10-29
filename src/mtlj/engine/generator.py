"""Mutant generation: run a battery of operators over records, keeping full provenance.

For every record and every (operator, intensity) in the battery, this makes one ``Mutant``
(see ``mtlj.data.mutants``), which carries everything needed to reproduce and explain it.

* The originals are returned and written next to the mutants, so scores can be compared in pairs.
* An operator that can't apply to a record (it changes nothing, or it fails) is skipped and
  logged, and the run carries on. Every skip is returned with its reason.
* Seeds are derived from the run's base seed, the record, the operator and the intensity, so a
  run is reproducible, and records don't all share the same random choices.
* An operator whose "dose" is countable (entity swap reports its substitutions) can land on the
  same dose at several intensities. Only the first mutant at each dose is kept, so a higher
  intensity always means a bigger change. The rest are skipped with reason ``repeated_dose``.
* ``write_generation`` also writes ``run.json``, so a run describes how it was made.
"""

import hashlib
import json
import logging
import platform
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from mtlj.data.mutants import Mutant
from mtlj.data.records import DatasetRecord
from mtlj.operators.base import MutationOperator

logger = logging.getLogger(__name__)

# Why a mutant was skipped.
NO_CHANGE = "no_change"
OPERATOR_ERROR = "operator_error"
REPEATED_DOSE = "repeated_dose"


@dataclass(frozen=True)
class BatteryItem:
    """One operator and the intensities to run it at."""

    operator: MutationOperator
    intensities: tuple[float, ...]


@dataclass(frozen=True)
class SkippedMutant:
    """A (record, operator, intensity) that produced no mutant, and why."""

    record_id: str
    operator_name: str
    intensity: float
    seed: int
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Everything a run produced: the originals, the mutants, and what was skipped.

    ``base_seed``, ``battery`` (operator name -> intensities) and ``drop_repeated_doses`` record
    how the run was made, so ``write_generation`` can save them in ``run.json``.
    """

    originals: list[DatasetRecord]
    mutants: list[Mutant]
    skipped: list[SkippedMutant]
    base_seed: int = 0
    battery: dict[str, list[float]] = field(default_factory=dict)
    drop_repeated_doses: bool = True

    def summary(self) -> dict[str, Any]:
        """Counts for a quick look at a run."""
        return {
            "records": len(self.originals),
            "mutants": len(self.mutants),
            "skipped": len(self.skipped),
            "skipped_by_reason": dict(Counter(s.reason for s in self.skipped)),
            "mutants_by_operator": dict(Counter(m.operator_name for m in self.mutants)),
        }


def default_battery() -> list[BatteryItem]:
    """The operators and intensities used for the study.

    Padding at 0.125 / 0.25 / 0.5 / 1.0 is 50 / 100 / 200 / 400 filler words. Entity swap at
    0.2 / 0.6 / 1.0 changes 1 / 3 / 5 claims. Imported here so spaCy isn't loaded at import time.
    """
    from mtlj.operators.degrading.entity_swap import EntitySwapOperator
    from mtlj.operators.preserving.verbosity_padding import VerbosityPaddingOperator

    return [
        BatteryItem(VerbosityPaddingOperator(), (0.125, 0.25, 0.5, 1.0)),
        BatteryItem(EntitySwapOperator(), (0.2, 0.6, 1.0)),
    ]


def derive_seed(base_seed: int, record_id: str, operator_name: str, intensity: float) -> int:
    """A repeatable 32-bit seed for one mutant.

    The same inputs always give the same seed, and different records or operators give
    different seeds, so the random choices aren't identical across the corpus.
    """
    key = f"{int(base_seed)}|{record_id}|{operator_name}|{float(intensity)!r}"
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")


def _check_battery(battery: Sequence[BatteryItem]) -> None:
    """Reject a battery that can't run, before any record is touched."""
    if not battery:
        raise ValueError("the battery is empty")
    seen: set[tuple[str, float]] = set()
    for item in battery:
        name = item.operator.metadata.name
        if not item.intensities:
            raise ValueError(f"{name} has no intensities")
        for intensity in item.intensities:
            if not 0 <= intensity <= 1:
                raise ValueError(f"{name}: intensity must be in [0, 1], got {intensity}")
            if (name, float(intensity)) in seen:
                raise ValueError(f"{name} at intensity {intensity} appears twice")
            seen.add((name, float(intensity)))


def _dose(mutant: Mutant) -> int | None:
    """How much a mutant changed, for operators that report it (entity swap lists its
    substitutions). ``None`` when the operator doesn't, e.g. padding, which is never deduplicated.
    """
    substitutions = mutant.details.get("substitutions")
    return len(substitutions) if isinstance(substitutions, (list, tuple)) else None


def generate_mutants(
    records: Iterable[DatasetRecord],
    battery: Sequence[BatteryItem],
    base_seed: int = 0,
    drop_repeated_doses: bool = True,
) -> GenerationResult:
    """Run every operator in ``battery`` on every record.

    An operator that changes nothing, or raises, is skipped and logged (see
    ``GenerationResult.skipped``). Other records and operators still run.

    With ``drop_repeated_doses`` (the default), a mutant whose dose (see ``_dose``) matches one
    already kept for the same record and operator is skipped with reason ``repeated_dose``. The
    lowest intensity that reaches a dose is the one kept, as long as the battery lists
    intensities from low to high.

    Raises:
        ValueError: if the battery is empty or invalid, or two records share an id.
    """
    battery = list(battery)
    _check_battery(battery)

    originals: list[DatasetRecord] = []
    mutants: list[Mutant] = []
    skipped: list[SkippedMutant] = []
    record_ids: set[str] = set()

    for record in records:
        if record.record_id in record_ids:
            raise ValueError(f"duplicate record_id {record.record_id!r}")
        record_ids.add(record.record_id)
        originals.append(record)

        for item in battery:
            name = item.operator.metadata.name
            kept_doses: dict[int, float] = {}  # dose -> the intensity that first reached it
            for intensity in item.intensities:
                seed = derive_seed(base_seed, record.record_id, name, intensity)
                try:
                    mutant = Mutant.generate(record, item.operator, intensity, seed)
                except Exception as exc:  # one bad record must not stop the whole run
                    logger.warning(
                        "skipped %s on %s at %s: %s: %s",
                        name,
                        record.record_id,
                        intensity,
                        type(exc).__name__,
                        exc,
                    )
                    skipped.append(
                        SkippedMutant(
                            record.record_id,
                            name,
                            intensity,
                            seed,
                            OPERATOR_ERROR,
                            {"error": f"{type(exc).__name__}: {exc}"},
                        )
                    )
                    continue

                if not mutant.changed:
                    logger.debug(
                        "skipped %s on %s at %s: no change", name, record.record_id, intensity
                    )
                    why = dict(mutant.details.get("skipped", {}))
                    skipped.append(
                        SkippedMutant(record.record_id, name, intensity, seed, NO_CHANGE, why)
                    )
                    continue

                dose = _dose(mutant)
                if drop_repeated_doses and dose is not None:
                    if dose in kept_doses:
                        logger.debug(
                            "skipped %s on %s at %s: same dose (%d) as intensity %s",
                            name,
                            record.record_id,
                            intensity,
                            dose,
                            kept_doses[dose],
                        )
                        why = {"dose": dose, "same_as_intensity": kept_doses[dose]}
                        skipped.append(
                            SkippedMutant(
                                record.record_id, name, intensity, seed, REPEATED_DOSE, why
                            )
                        )
                        continue
                    kept_doses[dose] = intensity

                mutants.append(mutant)

    result = GenerationResult(
        originals,
        mutants,
        skipped,
        base_seed=int(base_seed),
        battery={i.operator.metadata.name: [float(x) for x in i.intensities] for i in battery},
        drop_repeated_doses=drop_repeated_doses,
    )
    logger.info("generated mutants: %s", result.summary())
    return result


def _write_lines(path: Path, lines: Iterable[str]) -> None:
    # newline="\n" keeps the files identical on Windows and Linux.
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8", newline="\n")


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def run_manifest(result: GenerationResult) -> dict[str, Any]:
    """What it takes to re-run this generation: seed, battery, versions, and the summary.

    Nothing here changes from run to run (no timestamps), so the same run writes the same file.
    """
    return {
        "base_seed": result.base_seed,
        "battery": result.battery,
        "drop_repeated_doses": result.drop_repeated_doses,
        "summary": result.summary(),
        "versions": {
            "python": platform.python_version(),
            "spacy": _package_version("spacy"),
            "en_core_web_sm": _package_version("en-core-web-sm"),
        },
    }


def write_generation(result: GenerationResult, directory: Path) -> None:
    """Write a run to ``directory`` as three JSON Lines files and a ``run.json``.

    * ``originals.jsonl``: one ``DatasetRecord`` per line.
    * ``mutants.jsonl``: one ``Mutant`` per line, with full provenance. Its ``record_id``
      says which original it pairs with.
    * ``skipped.jsonl``: one skipped (record, operator, intensity) per line, with the reason.
    * ``run.json``: the base seed, battery, summary and library versions (see ``run_manifest``).
    """
    directory.mkdir(parents=True, exist_ok=True)
    _write_lines(directory / "originals.jsonl", (r.model_dump_json() for r in result.originals))
    _write_lines(directory / "mutants.jsonl", (m.model_dump_json() for m in result.mutants))
    _write_lines(
        directory / "skipped.jsonl",
        (json.dumps(asdict(s), sort_keys=True) for s in result.skipped),
    )
    manifest = json.dumps(run_manifest(result), indent=2, sort_keys=True)
    (directory / "run.json").write_text(manifest + "\n", encoding="utf-8", newline="\n")
