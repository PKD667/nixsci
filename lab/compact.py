"""Finished runs -> typed Parquet tables and their manifest rows.

Nix runs this inside a build, once per experiment, over the runs its lock names:

    python -m nixsci.lab.compact <output directory> <run store path>...

    <out>/<dataset>/<run id>.parquet   the columns the experiment declared, plus `run`, `seed`, `time`
    <out>/runs.json                    one manifest row per run: id, state, seed, parameters

One process for all runs: a derivation per run paid a sandbox and a pyarrow import each, about
0.7 s, which a sweep of a thousand runs turns into minutes of nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from nixsci.lab import load
from nixsci.lab.run import sha256_file
from nixsci.lab.schema import parse

ARROW = {"int": "int64", "float": "float64", "str": "string", "bool": "bool_"}


def _records(directory: Path) -> Path | None:
    for base in (directory, directory / "lab"):
        if (base / "records.jsonl").is_file():
            return base / "records.jsonl"
    return None


def row(manifest: dict[str, Any]) -> dict[str, Any]:
    keys = ("run", "seed", "state", "target", "started", "ended", "input_id", "replicate")
    return {**{k: manifest.get(k) for k in keys}, "params": manifest.get("params") or {}}


def compact_run(run: str | Path, out: str | Path) -> list[Path]:
    """Write a finished run's declared datasets under `out`; returns the files written."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    run, out = Path(run), Path(out)
    manifest = json.loads((run / "manifest.json").read_text())
    version = str(manifest.get("format", "1.0")).split(".")[0]
    if version not in ("1", "2"):
        raise ValueError(f"unsupported run format {manifest.get('format')!r}")
    if manifest.get("state") != "ok":
        raise ValueError(f"run {manifest.get('run')}: only a finished (ok) run can be compacted")
    records = _records(run)
    expected = manifest.get("records_sha256")
    if expected and records is not None and sha256_file(records) != expected:
        raise ValueError(
            f"run {manifest['run']}: records.jsonl does not match the hash sealed in its "
            "manifest (the data changed after the run)"
        )
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for dataset, columns in (manifest.get("schema") or {}).items():
        parsed = parse(dataset, columns)
        rows = load(run, dataset)
        key = (manifest.get("keys") or {}).get(dataset)
        if key:
            seen = set()
            for r in rows:
                ident = tuple(r["data"][c] for c in key)
                if ident in seen:
                    raise ValueError(f"run {manifest['run']}: dataset {dataset!r}: duplicate key {ident!r}")
                seen.add(ident)
        fields = [pa.field("run", pa.string()), pa.field("seed", pa.int64()), pa.field("time", pa.string())]
        fields += [pa.field(c, getattr(pa, ARROW[k])()) for c, (k, _) in parsed.items()]
        schema = pa.schema(fields)
        data: dict[str, list[Any]] = {f.name: [] for f in fields}
        for r in rows:
            data["run"].append(manifest["run"])
            data["seed"].append(manifest.get("seed"))
            data["time"].append(r["time"])
            for column in parsed:
                data[column].append(r["data"][column])
        target = out / dataset / f"{manifest['run']}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table(data, schema=schema), target)
        written.append(target)
    (out / "row.json").write_text(json.dumps(row(manifest), sort_keys=True))
    return written


def compact_runs(out: str | Path, runs: list[str | Path], expected: dict | None = None) -> None:
    """Compact every run into `out` and write their rows, in run order, to `out/runs.json`."""
    out, rows = Path(out), []
    for run in runs:
        manifest = json.loads((Path(run) / "manifest.json").read_text())
        if expected is not None and manifest["run"] in expected:
            from .publish import facts
            if facts(manifest) != expected[manifest["run"]]:
                raise ValueError(f"run {manifest['run']}: lock facts differ from the sealed manifest")
        compact_run(run, out)
        rows.append(json.loads((out / "row.json").read_text()))
    (out / "row.json").unlink(missing_ok=True)
    rows.sort(key=lambda r: r["run"])
    (out / "runs.json").write_text(json.dumps(rows, sort_keys=True))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--expect", type=Path)
    args = parser.parse_args()
    compact_runs(args.out, args.runs, json.loads(args.expect.read_text()) if args.expect else None)
