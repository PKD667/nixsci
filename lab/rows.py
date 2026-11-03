"""The rows of a run's datasets, checked against what the experiment declared.

A row is checked when it is recorded, by the recorder that wrote it, but the recorder may be another
language's, and the records may have been written by hand. So the same checks run again where the data
is used: by the executor before a run is accepted, and by compaction before it becomes a table. One
definition, so a run the executor accepts is a run compaction can read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import schema


def validate(dataset: str, columns: Mapping[str, str], key: Sequence[str] | None, records: Iterable[Mapping[str, Any]]) -> None:
    """Raise ValueError unless every record of `dataset` is a row of its columns, and no key repeats."""
    parsed = schema.parse(dataset, columns)
    seen: set[tuple[Any, ...]] = set()
    for record in records:
        if record.get("kind") != "value":
            raise ValueError(f"dataset {dataset!r}: a row is a value, not an artifact (record {record.get('id')!r})")
        try:
            schema.check(dataset, parsed, record["data"])
        except TypeError as error:  # a wrong type and a wrong shape are one kind of problem here
            raise ValueError(str(error)) from error
        if key:
            ident = schema.key_of(tuple(key), record["data"])
            if ident in seen:
                raise ValueError(f"dataset {dataset!r}: duplicate key {ident!r}")
            seen.add(ident)


def problems(records_file: str | Path, data: Mapping[str, Mapping[str, str]], keys: Mapping[str, Sequence[str]], limit: int = 5) -> list[str]:
    """What is wrong with a run's records, one sentence each (at most `limit`); empty when they are fine.

    `data` and `keys` are the experiment's declared datasets, as sealed in a manifest.
    """
    try:
        lines = Path(records_file).read_text().splitlines()
    except OSError:
        return []
    records = [json.loads(line) for line in lines if line.strip()]
    found = []
    for dataset, columns in data.items():
        try:
            validate(dataset, columns, keys.get(dataset), (r for r in records if r.get("name") == dataset))
        except ValueError as error:
            found.append(str(error))
    return found[:limit]
