"""A run recorded by hand, outside `nix-lab run`.

    with lab.Run("runs", "night-20261005", spec="measure.toml", seed=3) as run:
        run.record("size", {"n": 1000, "seconds": 1.5})

This creates `<root>/<app>/<name>/`, points `lab.record` at it with the spec's declared
datasets and keys, and writes `manifest.json` when the block ends (state `ok`, or `failed`
if it raised), so `nix-lab compact <root>` and `nix-lab analyze` read it like any run.
`app` defaults to the spec's `[experiment] name`.
"""

from __future__ import annotations

import json
import os
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
        raw = tomllib.loads(Path(spec).read_text()) if spec is not None else {}
        self.columns, self.keys = schema.declared(raw.get("data", {}))
        self.app = app or raw.get("experiment", {}).get("name")
        if not self.app:
            raise ValueError("give app=..., or a spec with an [experiment] name")
        self.name, self.seed, self.params = name, seed, dict(params or {})
        self.directory = Path(root) / self.app / name
        self.started = _utc()
        self._saved: dict[str, str | None] | None = None
        self._finished = False

    def __enter__(self) -> "Run":
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
            "started": self.started,
            "ended": _utc(),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "manifest.json"
        path.write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n")
        return path


def run_name(app: str, *, seed: int | None = None, stamp: str | None = None) -> str:
    """A run id in the runner's style: `<app>-<UTC stamp>[-s<seed>]`."""
    moment = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{app}-{moment}" + (f"-s{seed}" if seed is not None else "")
