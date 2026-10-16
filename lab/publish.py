"""A finished run enters the Nix store and the project's lock.

The run directory is added to the store by content, so its path and hash name exactly these bytes.
`lab.lock` next to the flake lists them per experiment, and Nix builds analyses from the
paths it names:

    { "demo": [ { "name": "demo-361f-r1", "path": "/nix/store/...-demo-361f-r1", "sha256": "sha256-..." } ] }
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from nixsci.deploy import nix as nixcli

_LOCK = threading.Lock()


def add_entry(lock: dict[str, list[dict[str, str]]], app: str, entry: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """The lock with `entry` among the runs of `app`: one entry per run name, sorted by name."""
    entries = {e["name"]: e for e in lock.get(app, [])}
    entries[entry["name"]] = entry
    return {**lock, app: [entries[name] for name in sorted(entries)]}


def read_lock(project: Path) -> dict[str, list[dict[str, str]]]:
    path = Path(project) / "lab.lock"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    if "format" not in raw:
        return raw  # v1: {experiment: [entries]}
    if str(raw["format"]).split(".")[0] != "2":
        raise ValueError(f"unsupported lab.lock format {raw['format']!r}")
    return raw["runs"]


def _write_lock(project: Path, lock: dict[str, list[dict[str, str]]]) -> None:
    fd, tmp = tempfile.mkstemp(dir=project, prefix=".lab.lock.tmp")
    with os.fdopen(fd, "w") as out:
        out.write(json.dumps({"format": "2.0", "runs": lock}, indent=2, sort_keys=True) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, project / "lab.lock")


def _job(seed, params) -> str:
    return json.dumps({"seed": seed, "params": params or {}}, sort_keys=True)


def retain(project: str | Path, app: str, closures: set[str], jobs: list[tuple[Any, dict]]) -> list[str]:
    """Drop the locked runs of `app` that are not of the experiment as it is now; returns their names.

    The lock lists runs of the current code (`closures`) whose seed and parameters are one of the
    spec's `jobs`. Any other run stays in the runs directory, in the store until collected, and in
    the lock's git history. A run recorded by hand has no closure and is kept.
    """
    wanted = {_job(seed, params) for seed, params in jobs}
    project = Path(project)
    with _LOCK:
        lock = read_lock(project)
        keep, dropped = [], []
        for entry in lock.get(app, []):
            m = entry.get("facts") or json.loads((Path(entry["path"]) / "manifest.json").read_text())
            closure = m.get("closure")
            current = closure in closures and _job(m.get("seed"), m.get("params")) in wanted
            (dropped if closure is not None and not current else keep).append(entry)
        if dropped:
            _write_lock(project, {**lock, app: keep})
    return [e["name"] for e in dropped]


def publish(run_dir: str | Path, project: str | Path) -> dict[str, str]:
    """Add a finished run to the Nix store and to `<project>/lab.lock`."""
    run_dir, project = Path(run_dir), Path(project)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    if manifest.get("state") != "ok":
        raise ValueError(f"{manifest.get('run')}: only a finished (ok) run is published")
    name = manifest["run"]
    path = nixcli.run("nix", None, "store", "add", "--name", name, str(run_dir)).stdout.decode().strip()
    sha256 = nixcli.path_hashes("nix", None, path)[path]
    entry = {"name": name, "path": path, "sha256": sha256,
             "facts": facts(manifest)}
    from .peers import root
    root(path)
    with _LOCK:
        _write_lock(project, add_entry(read_lock(project), manifest["app"], entry))
    return entry


FACTS = ("format", "run", "state", "closure", "seed", "params", "input_id", "replicate")


def facts(manifest: dict[str, Any]) -> dict[str, Any]:
    """The sealed manifest's identity fields, copied to the lock for checks without fetching data.

    Compaction checks this copy against the hashed manifest; a modified lock cannot make a run
    traced or current by changing its facts.
    """
    return {key: manifest.get(key) for key in FACTS}
