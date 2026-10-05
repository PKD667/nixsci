"""One finished run -> typed Parquet tables and its manifest row.

Nix runs this inside a build, once per locked run:

    python -m nixsci.lab.compact <run store path> <output directory>

    <out>/<dataset>/<run id>.parquet   the columns the experiment declared, plus `run`, `seed`, `time`
    <out>/row.json                     one run's manifest row: id, state, seed, parameters
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


if __name__ == "__main__":
    compact_run(sys.argv[1], sys.argv[2])
