"""Reproduce a run and compare it with the original: `repro` and `verify`.

Nothing here assumes bit-for-bit determinism. A re-run is a *replicate*: identical inputs,
a new sample. Dataset columns are compared exactly unless the spec declares them noisy
(`[data.x] noisy = { seconds = 0.25 }`, a relative tolerance), which is the honest model for
measurements with uncontrolled components such as MPI delays.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

import lab

from . import spec as spec_mod


def find_run(run: str | Path, runs_root: str | Path = "runs") -> Path:
    """A run directory given as a path, or as a run id searched under `runs_root`."""
    candidate = Path(run)
    if (candidate / "manifest.json").is_file():
        return candidate
    hits = sorted(Path(runs_root).glob(f"*/{run}"))
    hits = [h for h in hits if (h / "manifest.json").is_file()]
    if len(hits) != 1:
        raise SystemExit(f"run {str(run)!r}: {len(hits)} matches under {runs_root}/*/")
    return hits[0]


def locked_ref(locked: dict[str, Any]) -> str:
    """The flake reference string for a locked source (`rev` and `narHash` included)."""
    wire = json.dumps(json.dumps(locked, sort_keys=True))
    if "${" in wire:
        raise ValueError("locked source contains an interpolation")
    expr = f"builtins.flakeRefToString (builtins.fromJSON {wire})"
    result = subprocess.run(
        ["nix", "--extra-experimental-features", "nix-command", "eval", "--raw", "--expr", expr],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"cannot render the locked source: {result.stderr.strip()[-300:]}")
    return result.stdout


def bundle(run_dir: str | Path) -> dict[str, Any]:
    """Everything needed to run this measurement again, as plain data."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    spec_file = run_dir / "spec.toml"
    attr = spec_mod.load(spec_file).attr if spec_file.is_file() else manifest["app"]
    source = manifest.get("source") or {}
    return {
        "run": manifest["run"],
        "input_id": manifest.get("input_id"),
        "flake": locked_ref(source["locked"]) if source.get("locked") else None,
        "attr": attr,
        "closure": manifest.get("closure"),
        "params": manifest.get("params"),
        "seed": manifest.get("seed"),
        "schema": manifest.get("schema"),
        "keys": manifest.get("keys"),
        "tolerance": manifest.get("tolerance"),
        "spec_sha256": manifest.get("spec_sha256"),
        "records_sha256": manifest.get("records_sha256"),
        "machine": manifest.get("machine"),
        "started": manifest.get("started"),
        "command": f"nix-lab verify {run_dir}",
    }


def _rows(directory: Path, dataset: str, key: list[str] | None) -> dict[Any, dict[str, Any]]:
    rows = [r["data"] for r in lab.load(directory, dataset)]
    if key:
        return {tuple(r[c] for c in key): r for r in rows}
    return dict(enumerate(rows))


def compare(
    original: str | Path, replicate: str | Path, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compare two runs' datasets. Returns {"ok": bool, "datasets": {name: {...}}}."""
    original, replicate = Path(original), Path(replicate)
    manifest = manifest or json.loads((original / "manifest.json").read_text())
    tolerance = manifest.get("tolerance") or {}
    report: dict[str, Any] = {"ok": True, "datasets": {}}
    for dataset in manifest.get("schema") or {}:
        key = (manifest.get("keys") or {}).get(dataset)
        a, b = _rows(original, dataset, key), _rows(replicate, dataset, key)
        tol = tolerance.get(dataset, {})
        problems: list[str] = []
        worst: dict[str, float] = {}
        for ident in sorted(set(a) | set(b), key=repr):
            if ident not in a or ident not in b:
                problems.append(
                    f"row {ident!r} only in the {'replicate' if ident in b else 'original'}"
                )
                continue
            for column in a[ident]:
                x, y = a[ident][column], b[ident][column]
                if column in tol and x is not None and y is not None:
                    scale = max(abs(x), abs(y))
                    rel = abs(x - y) / scale if scale else 0.0
                    worst[column] = max(worst.get(column, 0.0), rel)
                    if rel > tol[column]:
                        problems.append(
                            f"row {ident!r} column {column!r}: {x!r} vs {y!r} "
                            f"(relative {rel:.3g} > {tol[column]})"
                        )
                elif x != y:
                    problems.append(f"row {ident!r} column {column!r}: {x!r} vs {y!r}")
        report["datasets"][dataset] = {
            "rows": [len(a), len(b)],
            "worst_relative": worst,
            "problems": problems[:20],
            "problem_count": len(problems),
        }
        report["ok"] = report["ok"] and not problems
    return report


def verify(run_dir: str | Path, *, log=print) -> dict[str, Any]:
    """Run one new replicate of `run_dir` from its locked source and compare the datasets."""
    from . import runner

    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest.get("state") != "ok":
        raise SystemExit(f"{manifest['run']}: only finished (ok) runs can be verified")
    info = bundle(run_dir)
    if not info["flake"]:
        raise SystemExit(f"{manifest['run']}: the manifest has no locked source to rebuild from")
    spec = spec_mod.load(run_dir / "spec.toml")
    one = dataclasses.replace(
        spec,
        flake=info["flake"],
        attr=info["attr"],
        seeds=() if manifest.get("seed") is None else (manifest["seed"],),
        params=dict(manifest.get("params") or {}),
        sweep={},
    )
    log(f"re-running {manifest['run']} from {info['flake']}")
    new = runner.run(one, run_dir.parent.parent, again=True, log=log)
    if not new:
        raise SystemExit("nothing ran")
    replicate = run_dir.parent / new[0]["run"]
    report = compare(run_dir, replicate, manifest)
    report["original"], report["replicate"] = manifest["run"], new[0]["run"]
    report["same_closure"] = new[0].get("closure") == manifest.get("closure")
    report["same_machine"] = (new[0].get("machine") or {}).get("hostname") == (
        manifest.get("machine") or {}
    ).get("hostname")
    report["replicate_state"] = new[0]["state"]
    report["ok"] = report["ok"] and new[0]["state"] == "ok"
    return report
