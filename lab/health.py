"""Did the experiment work, as its own rows say? Judged implicitly, with no configuration.

A runner that records failures as data exits 0 whatever happened, so the exit status alone cannot tell a
measurement from a launch that never started. The convention: a dataset with a boolean column named `ok` says,
row by row, whether that row worked. If every row says no, the run failed. If some do, that is a result, and
the run is only annotated. The notes are plain sentences, shown at the end of the run and kept in the manifest."""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def _ok_datasets(schema: Mapping[str, Any]) -> list[str]:
    """Datasets with a bool column `ok`. `schema` is spec.data: {dataset: {column: type}}, as sealed in a
    manifest (the Nix declaration's `{"columns": {...}}` wrapper is accepted too)."""
    found = []
    for name, body in schema.items():
        if not isinstance(body, Mapping):
            continue
        columns = body["columns"] if isinstance(body.get("columns"), Mapping) else body
        if columns.get("ok") == "bool":
            found.append(name)
    return found


def judge(records: Iterable[Mapping[str, Any]], schema: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """`(failed, notes)` for the records of one run. `schema` is the experiment's declared datasets."""
    by: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for record in records:
        by[record.get("name")].append(record.get("data") or {})
    failed, notes = False, []
    for name in _ok_datasets(schema):
        rows = by.get(name, [])
        if not rows:
            notes.append(f"the job recorded no rows of '{name}'")
            continue
        bad = [r for r in rows if r.get("ok") is not True]
        if not bad:
            continue
        verdicts = collections.Counter(str(r["verdict"]) for r in bad if "verdict" in r)
        detail = f" (verdict: {', '.join(f'{v} {n}' for v, n in verdicts.most_common())})" if verdicts else ""
        if len(bad) == len(rows):
            failed = True
            notes.append(f"all {len(rows)} rows of '{name}' have ok=false{detail}")
        else:
            notes.append(f"{len(bad)} of {len(rows)} rows of '{name}' have ok=false{detail}")
    return failed, notes


def check(records_file: Path, schema: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """`judge` over a records.jsonl; a missing file is judged as no records."""
    try:
        lines = Path(records_file).read_text().splitlines()
    except OSError:
        lines = []
    return judge([json.loads(line) for line in lines if line.strip()], schema)
