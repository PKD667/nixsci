"""Declared datasets: column names and types, checked when a row is recorded.

A schema is a mapping of column name to type: `int`, `float`, `str` or `bool`,
with a trailing `?` for a column that may be null (`"float?"`).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

RESERVED = ("run", "seed", "time")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TYPES = ("int", "float", "str", "bool")


def parse(name: str, columns: Mapping[str, str]) -> dict[str, tuple[str, bool]]:
    """Validate a dataset declaration -> {column: (type, nullable)}."""
    if not isinstance(columns, Mapping) or not columns:
        raise ValueError(f"dataset {name!r}: columns must be a non-empty table")
    parsed = {}
    for column, spec in columns.items():
        if not _NAME.fullmatch(column) or column in RESERVED:
            raise ValueError(
                f"dataset {name!r}: invalid column name {column!r} (reserved: {RESERVED})"
            )
        kind = str(spec)
        nullable = kind.endswith("?")
        kind = kind.rstrip("?")
        if kind not in TYPES:
            raise ValueError(
                f"dataset {name!r}: column {column!r} has type {spec!r}; use one of {TYPES}"
            )
        parsed[column] = (kind, nullable)
    return parsed


def _fits(kind: str, value: Any) -> bool:
    if isinstance(value, bool):
        return kind == "bool"
    if kind == "int":
        return isinstance(value, int)
    if kind == "float":
        return isinstance(value, (int, float))
    if kind == "str":
        return isinstance(value, str)
    return False


def check(name: str, columns: Mapping[str, tuple[str, bool]], row: Any) -> None:
    """Raise unless `row` is a dict with exactly the declared columns and fitting values."""
    if not isinstance(row, dict):
        raise TypeError(f"dataset {name!r} takes a row (dict of columns), got {type(row).__name__}")
    extra, missing = set(row) - set(columns), set(columns) - set(row)
    if extra or missing:
        raise ValueError(
            f"dataset {name!r}: unexpected columns {sorted(extra)}, missing {sorted(missing)}"
        )
    for column, (kind, nullable) in columns.items():
        value = row[column]
        if value is None:
            if not nullable:
                raise ValueError(f"dataset {name!r}: column {column!r} is not nullable")
        elif not _fits(kind, value):
            raise TypeError(f"dataset {name!r}: column {column!r} is {kind}, got {value!r}")
