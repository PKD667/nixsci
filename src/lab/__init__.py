"""lab.record(...): write experimental data where nix-lab will collect it.

    import lab
    lab.record("loss", 0.25, epoch=3)
    lab.record("weights", numpy_array)        # stored as an artifact
    lab.params()                              # this run's parameters
"""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = 1
_counter = itertools.count()


def _dir() -> Path:
    root = os.environ.get("NIX_LAB_DIR") or "lab"
    path = Path(root)
    (path / "artifacts").mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _append(entry: dict[str, Any]) -> None:
    line = (json.dumps(entry, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
    fd = os.open(_dir() / "records.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _store(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    target = _dir() / "artifacts" / digest
    if not target.exists():
        tmp = target.with_name(f".{digest}.{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return digest


def _jsonable(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return all(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _jsonable(v) for k, v in value.items())
    return False


def record(name: str, value: Any, **tags: Any) -> None:
    """Record `value` under `name`. JSON-like values are stored inline; numpy
    arrays, bytes and file paths become content-addressed artifacts."""
    if not isinstance(name, str) or not name:
        raise ValueError("record name must be a non-empty string")
    if not _jsonable(tags):
        raise TypeError("tags must be JSON-like")
    entry: dict[str, Any] = {"v": VERSION, "id": f"{os.getpid()}-{next(_counter)}",
                             "time": _now(), "name": name, "tags": tags}
    if _jsonable(value):
        entry.update(kind="value", data=value)
    else:
        blob, media = _blob(value)
        entry.update(kind="artifact", sha256=_store(blob), bytes=len(blob), media=media)
    _append(entry)


def _blob(value: Any) -> tuple[bytes, str]:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value), "application/octet-stream"
    if isinstance(value, os.PathLike):
        return Path(value).read_bytes(), "application/octet-stream"
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None and isinstance(value, np.generic):
        return _blob(value.item())
    if np is not None and isinstance(value, np.ndarray):
        out = io.BytesIO()
        np.save(out, value, allow_pickle=False)
        return out.getvalue(), "application/x-npy"
    raise TypeError(f"cannot record {type(value).__name__}: only JSON values, numpy arrays, "
                    "bytes and paths are supported (no pickle)")


def params() -> dict[str, Any]:
    return json.loads(os.environ.get("NIX_LAB_PARAMS", "{}"))


def seed() -> int | None:
    value = os.environ.get("NIX_LAB_SEED")
    return int(value) if value not in (None, "") else None


def load(run: str | os.PathLike[str], name: str | None = None) -> list[dict[str, Any]]:
    """Read the records of one run directory (the `lab/` dir or its parent)."""
    root = Path(run)
    if (root / "lab").is_dir():
        root = root / "lab"
    path = root / "records.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    return [r for r in rows if name is None or r["name"] == name]


def artifact(run: str | os.PathLike[str], row: dict[str, Any]) -> bytes:
    root = Path(run)
    root = root / "lab" if (root / "lab").is_dir() else root
    return (root / "artifacts" / row["sha256"]).read_bytes()


def runs(root: str | os.PathLike[str], **where: Any) -> list[Path]:
    """Run directories under `root` whose manifest matches `where`
    (e.g. `app="toy"`, `seed=3`, `params__lr=0.1`)."""
    found = []
    for manifest in sorted(Path(root).glob("**/manifest.json")):
        data = json.loads(manifest.read_text())
        if all(_get(data, key) == want for key, want in where.items()):
            found.append(manifest.parent)
    return found


def _get(data: dict[str, Any], key: str) -> Any:
    for part in key.split("__"):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


__all__ = ["record", "params", "seed", "load", "artifact", "runs"]
