"""Run an analysis spec's R pipelines over exactly the data it `use`s.

An analysis names experiments under `[use]`. Each alias resolves through that experiment's
`<spec>.lab.lock`, so the analysis reads the locked runs and nothing else. Before R starts, this
module checks that the store holds what the locks name, then builds a *view*: a directory of
symlinks to just those runs' Parquet files, plus `runs.json` per alias.

    <out>/.view/<alias>/<dataset>/<run>.parquet     -> the store
    <out>/.view/<alias>/runs.json                   one row per locked run
    <out>/.view/use.json                            what each alias resolved to

The script runs with `NIX_LAB_VIEW` (that view) and `NIX_LAB_OUT` (`<out>/<pipeline>/`) and with
no variable naming the store, so the `nixsci` R package can open an alias and nothing else. A
`provenance.json` beside the outputs records the script hash, every input run with its manifest
hash, the locks they came through, the R version and the exit code.

A pipeline is skipped when its previous run succeeded and nothing it depends on has changed: the
script, its `deps` files, and the set of locked runs (the `fingerprint` in provenance.json).
`force=True` reruns. Run it inside the pinned environment (the flake's `r-env`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nixsci.lab import lock
from nixsci.lab import spec as spec_mod
from nixsci.lab.run import sha256_file
from nixsci.lab.spec import Spec

_HIDDEN = {"NIXSCI_STORE", "NIXSCI_INPUTS", "NIX_LAB_VIEW", "NIX_LAB_OUT"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fingerprint(script: Path, deps: dict[str, str], inputs: list[dict[str, str]]) -> str:
    body = {"script": _sha(script), "deps": deps, "inputs": inputs}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def resolve(spec: Spec, runs_root: Path) -> dict[str, dict[str, Any]]:
    """alias -> {spec, lock, lock_sha256}; stops if the store lacks what an experiment lock names."""
    resolved: dict[str, dict[str, Any]] = {}
    for alias, target in spec.use.items():
        measure = spec_mod.load(target)
        if measure.kind != "experiment":
            raise SystemExit(f"use.{alias}: {target} is not an experiment spec")
        data = lock.read(target)
        problems = lock.check(data, runs_root, Path(os.devnull))
        if problems:
            lines = [f"  {kind}: {what}: {detail}" for kind, what, detail in problems[:10]]
            raise SystemExit(
                f"use.{alias}: the store does not hold what {lock.path_for(target).name} locks:\n"
                + "\n".join(lines)
                + f"\nrun `nixsci lab pull <remote> {target}` or `nixsci lab build {target}`"
            )
        resolved[alias] = {"spec": target, "lock": data, "lock_sha256": sha256_file(lock.path_for(target))}
    return resolved


def _row(manifest: dict[str, Any]) -> dict[str, Any]:
    keys = ("run", "seed", "state", "target", "started", "ended", "input_id", "replicate")
    return {**{k: manifest.get(k) for k in keys}, "params": manifest.get("params") or {}}


def build_view(
    resolved: dict[str, dict[str, Any]], runs_root: Path, data_root: Path, view: Path
) -> dict[str, Any]:
    shutil.rmtree(view, ignore_errors=True)
    view.mkdir(parents=True)
    manifests = {
        (alias, entry["run"]): json.loads(
            (runs_root / entry["app"] / entry["run"] / "manifest.json").read_text()
        )
        for alias, item in resolved.items()
        for entry in item["lock"]["run"]
    }
    if any(m.get("schema") for m in manifests.values()):
        from .compact import compact

        compact(runs_root, data_root)
    summary: dict[str, Any] = {}
    for alias, item in resolved.items():
        base = view / alias
        base.mkdir()
        rows: list[dict[str, Any]] = []
        datasets: set[str] = set()
        for entry in item["lock"]["run"]:
            manifest = manifests[(alias, entry["run"])]
            rows.append(_row(manifest))
            for dataset in manifest.get("schema") or {}:
                source = data_root / entry["app"] / dataset / f"{entry['run']}.parquet"
                if not source.is_file():
                    raise SystemExit(f"use.{alias}: {source} was not compacted")
                (base / dataset).mkdir(exist_ok=True)
                (base / dataset / source.name).symlink_to(source)
                datasets.add(dataset)
        (base / "runs.json").write_text(json.dumps(rows, sort_keys=True))
        summary[alias] = {
            "spec": str(item["spec"]),
            "lock_sha256": item["lock_sha256"],
            "runs": [r["run"] for r in rows],
            "datasets": sorted(datasets),
        }
    (view / "use.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def analyze(
    spec: Spec,
    runs: Path,
    data: Path,
    out: Path,
    only: str | None = None,
    force: bool = False,
) -> dict[str, tuple[int, bool]]:
    """Returns {pipeline: (exit code, skipped)}."""
    if spec.kind != "analysis":
        raise SystemExit(f"{spec.path.name} is an experiment spec; analyses have an [analysis] table")
    rscript = os.environ.get("NIX_LAB_RSCRIPT") or shutil.which("Rscript")
    if not rscript:
        raise SystemExit("Rscript not found: run inside the pinned R environment")
    resolved = resolve(spec, runs)
    view = out / ".view"
    used = build_view(resolved, runs, data, view)
    inputs = [
        {"alias": alias, "run": entry["run"], "manifest_sha256": entry["manifest_sha256"]}
        for alias, item in sorted(resolved.items())
        for entry in item["lock"]["run"]
    ]
    results = {}
    base = spec.path.parent
    for name, pipeline in spec.pipelines.items():
        if only and name != only:
            continue
        script = (base / pipeline["script"]).resolve()
        deps = {d: _sha((base / d).resolve()) for d in pipeline["deps"]}
        target = out / name
        target.mkdir(parents=True, exist_ok=True)
        fingerprint = _fingerprint(script, deps, inputs)
        previous = target / "provenance.json"
        if not force and previous.exists():
            before = json.loads(previous.read_text())
            if before.get("fingerprint") == fingerprint and before.get("exit_code") == 0:
                results[name] = (0, True)
                continue
        started = _utc()
        env = {k: v for k, v in os.environ.items() if k not in _HIDDEN}
        env.update(NIX_LAB_VIEW=str(view), NIX_LAB_OUT=str(target))
        code = subprocess.run([rscript, str(script)], env=env, cwd=target).returncode
        version = subprocess.run([rscript, "--version"], capture_output=True, text=True)
        previous.write_text(
            json.dumps(
                {
                    "pipeline": name,
                    "script": str(script.relative_to(base)),
                    "script_sha256": _sha(script),
                    "deps": deps,
                    "use": {a: {k: s[k] for k in ("spec", "lock_sha256")} for a, s in used.items()},
                    "inputs": inputs,
                    "fingerprint": fingerprint,
                    "r": (version.stderr or version.stdout).strip(),
                    "started": started,
                    "ended": _utc(),
                    "exit_code": code,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        results[name] = (code, False)
    return results
