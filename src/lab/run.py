"""A run recorded by hand, outside `nix-lab run`.

    with lab.Run("runs", "night-20261005", spec="measure.toml", seed=3) as run:
        run.record("size", {"n": 1000, "seconds": 1.5})

This creates `<root>/<app>/<name>/`, points `lab.record` at it with the spec's declared
datasets and keys, and writes `manifest.json` when the block ends (state `ok`, or `failed`
if it raised), so `nix-lab compact <root>` and `nix-lab analyze` read it like any run.
`app` defaults to the spec's `[experiment] name`.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import schema

_ENV = (
    "NIX_LAB_DIR",
    "NIX_LAB_RUN",
    "NIX_LAB_SEED",
    "NIX_LAB_PARAMS",
    "NIX_LAB_SCHEMA",
    "NIX_LAB_KEYS",
)
STATES = ("ok", "failed", "incomplete")


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def machine() -> dict[str, Any]:
    """Facts about this machine that matter when comparing measurements (SPEC.md, machine.json)."""
    cpu, mem = "", None
    try:
        with open("/proc/cpuinfo") as f:
            cpu = next((l.split(":", 1)[1].strip() for l in f if l.startswith("model name")), "")
        with open("/proc/meminfo") as f:
            mem = next((int(l.split()[1]) for l in f if l.startswith("MemTotal")), None)
    except OSError:
        pass
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpus": os.cpu_count(),
        "cpu": cpu,
        "mem_kb": mem,
    }


def seal(
    directory: str | os.PathLike[str], manifest: dict[str, Any], *, local: bool
) -> dict[str, Any]:
    """Add what makes a finished run checkable: the hash of its records and the machine it ran on.

    `machine.json` (written by the mkExperiment wrapper on the host that ran the program) wins;
    `local=True` falls back to this machine, which is right only when the run happened here.
    """
    directory = Path(directory)
    manifest["records_sha256"] = None
    machine_file = None
    for base in (directory, directory / "lab"):
        if (base / "records.jsonl").is_file() and manifest["records_sha256"] is None:
            manifest["records_sha256"] = sha256_file(base / "records.jsonl")
        if (base / "machine.json").is_file() and machine_file is None:
            machine_file = base / "machine.json"
    if machine_file is not None:
        manifest["machine"] = json.loads(machine_file.read_text())
    else:
        manifest["machine"] = machine() if local else None
    return manifest


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Run:
    def __init__(
        self,
        root: str | os.PathLike[str],
        name: str,
        *,
        spec: str | os.PathLike[str] | None = None,
        app: str | None = None,
        seed: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        if not name or "/" in name or name in (".", ".."):
            raise ValueError(f"invalid run name {name!r}")
        self.spec_bytes = Path(spec).read_bytes() if spec is not None else None
        raw = tomllib.loads(self.spec_bytes.decode()) if self.spec_bytes is not None else {}
        self.columns, self.keys = schema.declared(raw.get("data", {}))
        self.tolerance = schema.tolerances(raw.get("data", {}))
        self.app = app or raw.get("experiment", {}).get("name")
        if not self.app:
            raise ValueError("give app=..., or a spec with an [experiment] name")
        self.name, self.seed, self.params = name, seed, dict(params or {})
        self.directory = Path(root) / self.app / name
        self.started = _utc()
        self._saved: dict[str, str | None] | None = None
        self._finished = False

    def __enter__(self) -> "Run":
        from . import _seen

        _seen.clear()  # keys are unique per run, not per process
        self.directory.mkdir(parents=True, exist_ok=True)
        self._saved = {k: os.environ.get(k) for k in _ENV}
        env = {
            "NIX_LAB_DIR": str(self.directory),
            "NIX_LAB_RUN": self.name,
            "NIX_LAB_PARAMS": json.dumps(self.params, sort_keys=True),
            "NIX_LAB_SEED": None if self.seed is None else str(self.seed),
            "NIX_LAB_SCHEMA": json.dumps(self.columns, sort_keys=True) if self.columns else None,
            "NIX_LAB_KEYS": json.dumps(self.keys, sort_keys=True) if self.keys else None,
        }
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if not self._finished:
                self.finish("ok" if exc_type is None else "failed")
        finally:
            from . import _seen

            _seen.clear()
            for key, value in (self._saved or {}).items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def record(self, name: str, value: Any, **tags: Any) -> None:
        from . import record

        record(name, value, **tags)

    def finish(self, state: str = "ok") -> Path:
        if state not in STATES:
            raise ValueError(f"invalid run state {state!r}; one of {STATES}")
        self._finished = True
        manifest = {
            "v": 1,
            "app": self.app,
            "run": self.name,
            "state": state,
            "seed": self.seed,
            "params": self.params,
            "schema": self.columns,
            "keys": self.keys,
            "tolerance": self.tolerance,
            "started": self.started,
            "ended": _utc(),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.spec_bytes is not None:
            (self.directory / "spec.toml").write_bytes(self.spec_bytes)
            manifest["spec_sha256"] = hashlib.sha256(self.spec_bytes).hexdigest()
        seal(self.directory, manifest, local=True)
        path = self.directory / "manifest.json"
        path.write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n")
        return path


def run_name(app: str, *, seed: int | None = None, stamp: str | None = None) -> str:
    """A run id in the runner's style: `<app>-<UTC stamp>[-s<seed>]`."""
    moment = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{app}-{moment}" + (f"-s{seed}" if seed is not None else "")
