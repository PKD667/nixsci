"""Collected runs -> typed Parquet, one partition per run.

    <out>/<app>/<dataset>/<run id>.parquet

Columns are the dataset's declared columns plus `run`, `seed` and `time`.
Needs pyarrow (`nix-lab[arrow]`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lab import load
from lab.schema import parse

ARROW = {"int": "int64", "float": "float64", "str": "string", "bool": "bool_"}


def _manifests(runs_root: Path):
    for path in sorted(runs_root.glob("*/*/manifest.json")):
        yield path.parent, json.loads(path.read_text())


def compact(runs_root: str | Path, out: str | Path) -> list[Path]:
    """Write every finished run's declared datasets under `out`; returns the files written."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise SystemExit("compaction needs pyarrow: install nix-lab[arrow]") from error
    runs_root, out = Path(runs_root), Path(out)
    written = []
    for directory, manifest in _manifests(runs_root):
        if manifest.get("state") != "ok":
            continue
        for dataset, columns in (manifest.get("schema") or {}).items():
            parsed = parse(dataset, columns)
            rows = load(directory, dataset)
            key = (manifest.get("keys") or {}).get(dataset)
            if key:
                seen = set()
                for row in rows:
                    ident = tuple(row["data"][c] for c in key)
                    if ident in seen:
                        raise ValueError(
                            f"run {manifest['run']}: dataset {dataset!r}: duplicate key {ident!r}"
                        )
                    seen.add(ident)
            fields = [
                pa.field("run", pa.string()),
                pa.field("seed", pa.int64()),
                pa.field("time", pa.string()),
            ]
            fields += [pa.field(c, getattr(pa, ARROW[k])()) for c, (k, _) in parsed.items()]
            schema = pa.schema(fields)
            data: dict[str, list[Any]] = {f.name: [] for f in fields}
            for row in rows:
                data["run"].append(manifest["run"])
                data["seed"].append(manifest.get("seed"))
                data["time"].append(row["time"])
                for column in parsed:
                    data[column].append(row["data"][column])
            target = out / manifest["app"] / dataset / f"{manifest['run']}.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.table(data, schema=schema), target)
            written.append(target)
    return written
