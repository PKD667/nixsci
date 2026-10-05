"""An experiment's spec, as Nix evaluated it, and the concrete jobs it expands to.

Specs are Nix values (`nixsci.lib.lab`). Nix writes each experiment's spec to a JSON file in the
store and hands its path to the executor, which reads it here. The same file is sealed into every
run it produces, as `spec.json`.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nixsci.lab import schema

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_KNOWN = {"kind", "name", "systems", "seeds", "replicates", "params", "sweep", "resources", "outputs", "data"}


@dataclass(frozen=True)
class Job:
    index: int
    seed: int | None
    params: dict[str, Any]


@dataclass(frozen=True)
class Spec:
    name: str
    seeds: tuple[int, ...]
    params: dict[str, Any]
    sweep: dict[str, list[Any]]
    resources: dict[str, Any]
    raw: dict[str, Any]
    data: dict[str, dict[str, str]] = field(default_factory=dict)
    keys: dict[str, list[str]] = field(default_factory=dict)
    tolerance: dict[str, dict[str, float]] = field(default_factory=dict)
    replicates: int = 1

    @property
    def provider(self) -> str:
        return str(self.resources.get("provider", "local"))

    def json_bytes(self) -> bytes:
        return json.dumps(self.raw, sort_keys=True, separators=(",", ":")).encode()

    def jobs(self) -> list[Job]:
        keys = sorted(self.sweep)
        grid = [
            dict(zip(keys, combo)) for combo in itertools.product(*(self.sweep[k] for k in keys))
        ] or [{}]
        seeds: tuple[int | None, ...] = self.seeds or (None,)
        return [
            Job(i, seed, {**self.params, **point})
            for i, (point, seed) in enumerate(itertools.product(grid, seeds))
        ]


def from_json(raw: dict[str, Any]) -> Spec:
    if not isinstance(raw, dict) or not _NAME.fullmatch(str(raw.get("name", ""))):
        raise ValueError(f"a spec needs a name matching {_NAME.pattern}")
    where = raw["name"]
    if raw.get("kind") != "experiment":
        raise ValueError(f"{where}: kind must be 'experiment' (analyses are Nix derivations, not run here)")
    unknown = set(raw) - _KNOWN
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {sorted(unknown)}; known: {sorted(_KNOWN)}")
    seeds = raw.get("seeds", [])
    if not isinstance(seeds, list) or not all(
        isinstance(s, int) and not isinstance(s, bool) for s in seeds
    ):
        raise ValueError(f"{where}: seeds must be a list of integers")
    sweep = raw.get("sweep", {})
    if not all(isinstance(v, list) and v for v in sweep.values()):
        raise ValueError(f"{where}: every sweep entry must be a non-empty list")
    replicates = raw.get("replicates", 1)
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1:
        raise ValueError(f"{where}: replicates must be an integer >= 1")
    data, keys = schema.declared(raw.get("data", {}))
    return Spec(
        name=where, seeds=tuple(seeds), params=dict(raw.get("params", {})), sweep=dict(sweep),
        resources=dict(raw.get("resources", {})), raw=raw, data=data, keys=keys,
        tolerance=schema.tolerances(raw.get("data", {})), replicates=replicates,
    )


def read(spec_json: str | Path) -> Spec:
    """A spec from the JSON file Nix wrote, or the one sealed into a run."""
    return from_json(json.loads(Path(spec_json).read_text()))
