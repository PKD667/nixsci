"""Spec (TOML) -> an experiment's concrete jobs, or an analysis' declared inputs.

An experiment spec has an `[experiment]` table and produces runs. An analysis spec has an
`[analysis]` table, names under `[use]` the experiments whose locked data it reads, and holds R
pipelines. The two never mix: an analysis never measures, an experiment never aggregates.
"""

from __future__ import annotations

import itertools
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nixsci.lab import schema

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_EXPERIMENT = {"experiment", "params", "sweep", "resources", "outputs", "data"}
_ANALYSIS = {"analysis", "use", "pipeline"}


@dataclass(frozen=True)
class Job:
    index: int
    seed: int | None
    params: dict[str, Any]


@dataclass(frozen=True)
class Spec:
    name: str
    flake: str
    attr: str
    seeds: tuple[int, ...]
    params: dict[str, Any]
    sweep: dict[str, list[Any]]
    resources: dict[str, Any]
    outputs: dict[str, Any]
    path: Path
    data: dict[str, dict[str, str]] = field(default_factory=dict)
    keys: dict[str, list[str]] = field(default_factory=dict)
    tolerance: dict[str, dict[str, float]] = field(default_factory=dict)
    replicates: int = 1
    pipelines: dict[str, dict[str, Any]] = field(default_factory=dict)
    kind: str = "experiment"
    use: dict[str, Path] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def provider(self) -> str:
        return str(self.resources.get("provider", "local"))

    def jobs(self) -> list[Job]:
        if self.kind != "experiment":
            raise ValueError(f"{self.path}: only an experiment spec has jobs")
        keys = sorted(self.sweep)
        grid = [
            dict(zip(keys, combo)) for combo in itertools.product(*(self.sweep[k] for k in keys))
        ] or [{}]
        seeds: tuple[int | None, ...] = self.seeds or (None,)
        return [
            Job(i, seed, {**self.params, **point})
            for i, (point, seed) in enumerate(itertools.product(grid, seeds))
        ]


def _analysis(path: Path, raw: dict[str, Any]) -> Spec:
    unknown = set(raw) - _ANALYSIS
    if unknown:
        raise ValueError(f"{path}: unknown table(s) {sorted(unknown)} in an analysis; known: {sorted(_ANALYSIS)}")
    head = raw["analysis"]
    if not isinstance(head, dict) or not _NAME.fullmatch(str(head.get("name", ""))):
        raise ValueError(f"{path}: [analysis] needs a name matching {_NAME.pattern}")
    use: dict[str, Path] = {}
    for alias, target in raw.get("use", {}).items():
        if not _ALIAS.fullmatch(alias):
            raise ValueError(f"{path}: [use] alias {alias!r} must be an R identifier ({_ALIAS.pattern})")
        if not isinstance(target, str):
            raise ValueError(f"{path}: use.{alias} must be the path of an experiment spec")
        use[alias] = (path.parent / target).resolve()
    if not use:
        raise ValueError(f"{path}: an analysis needs at least one [use] entry: it reads only what it uses")
    pipelines = {}
    for pname, body in raw.get("pipeline", {}).items():
        if not _NAME.fullmatch(pname) or not isinstance(body.get("script"), str):
            raise ValueError(f"{path}: [pipeline.{pname}] needs a valid name and a script")
        if "inputs" in body:
            raise ValueError(f"{path}: [pipeline.{pname}] has no `inputs`: it reads what [use] declares")
        pipelines[pname] = {"script": body["script"], "deps": list(body.get("deps", []))}
    return Spec(
        name=head["name"],
        flake="",
        attr="",
        seeds=(),
        params={},
        sweep={},
        resources={},
        outputs={},
        path=path,
        pipelines=pipelines,
        kind="analysis",
        use=use,
    )


def load(path: str | Path) -> Spec:
    path = Path(path).resolve()
    raw = tomllib.loads(path.read_text())
    if "analysis" in raw and "experiment" in raw:
        raise ValueError(f"{path}: a spec is an experiment or an analysis, not both")
    if "analysis" in raw:
        return _analysis(path, raw)
    unknown = set(raw) - _EXPERIMENT
    if unknown:
        hint = " (pipelines and [use] belong in an analysis spec, which has an [analysis] table)" if unknown & _ANALYSIS else ""
        raise ValueError(f"{path}: unknown table(s) {sorted(unknown)}; known: {sorted(_EXPERIMENT)}{hint}")
    exp = raw.get("experiment")
    if not isinstance(exp, dict) or not _NAME.fullmatch(str(exp.get("name", ""))):
        raise ValueError(f"{path}: [experiment] needs a name matching {_NAME.pattern}")
    seeds = exp.get("seeds", [])
    if not isinstance(seeds, list) or not all(
        isinstance(s, int) and not isinstance(s, bool) for s in seeds
    ):
        raise ValueError(f"{path}: experiment.seeds must be a list of integers")
    sweep = raw.get("sweep", {})
    if not all(isinstance(v, list) and v for v in sweep.values()):
        raise ValueError(f"{path}: every [sweep] entry must be a non-empty list")
    flake = str(exp.get("flake", "."))
    if flake.startswith((".", "/")):
        flake = str((path.parent / flake).resolve())
    data, keys = schema.declared(raw.get("data", {}))
    tolerance = schema.tolerances(raw.get("data", {}))
    replicates = exp.get("replicates", 1)
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1:
        raise ValueError(f"{path}: experiment.replicates must be an integer >= 1")
    return Spec(
        name=exp["name"],
        flake=flake,
        attr=str(exp.get("attr", exp["name"])),
        seeds=tuple(seeds),
        params=dict(raw.get("params", {})),
        sweep=dict(sweep),
        resources=dict(raw.get("resources", {})),
        outputs=dict(raw.get("outputs", {})),
        path=path,
        data=data,
        keys=keys,
        tolerance=tolerance,
        replicates=replicates,
    )
