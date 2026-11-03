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
from .run import read_manifest

_LOCK = threading.Lock()


def add_entry(lock: dict[str, list[dict[str, str]]], app: str, entry: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """The lock with `entry` among the runs of `app`: one entry per run name, sorted by name.

    A name that is already there with other contents is refused. Two machines that each measured the
    same input are two different runs with one name, and replacing one with the other would lose a
    measurement without a word.
    """
    entries = {e["name"]: e for e in lock.get(app, [])}
    old = entries.get(entry["name"])
    if old is not None and old["sha256"] != entry["sha256"]:
        raise ValueError(
            f"{entry['name']}: lab.lock already lists a run of that name with other contents (another "
            "machine measured this input too). Update lab.lock from git and run again; this run stays "
            "in .nixsci/runs."
        )
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


def retain(project: str | Path, app: str, current: set[str]) -> list[str]:
    """Drop the locked runs of `app` that did not measure the experiment as it is now; returns their names.

    The lock lists the runs whose input_id is one of `current` (store.current_ids): the current code,
    a job of the current spec, its schema, keys and span. Any other run stays in the runs directory, in
    the store until collected, and in the lock's git history. A run recorded by hand has no closure
    and is kept.
    """
    project = Path(project)
    with _LOCK:
        lock = read_lock(project)
        keep, dropped = [], []
        for entry in lock.get(app, []):
            m = entry.get("facts") or read_manifest(Path(entry["path"]) / "manifest.json")
            traced = m.get("closure") is not None
            (keep if not traced or m.get("input_id") in current else dropped).append(entry)
        if dropped:
            _write_lock(project, {**lock, app: keep})
    return [e["name"] for e in dropped]


def publish(run_dir: str | Path, project: str | Path) -> dict[str, str]:
    """Add a finished run to the Nix store and to `<project>/lab.lock`."""
    run_dir, project = Path(run_dir), Path(project)
    manifest = read_manifest(run_dir / "manifest.json")
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
