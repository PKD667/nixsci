"""Where nix-lab keeps data: one store per machine, never inside a project.

    $NIX_LAB_STORE, else $XDG_DATA_HOME/nix-lab, else ~/.local/share/nix-lab
        runs/<app>/<run>/        immutable, sealed runs (what `lab.Run` and `nix-lab run` write)
        data/<app>/<dataset>/    Parquet derived from the runs (rebuildable)
        analysis/<app>/<name>/   pipeline outputs plus provenance.json (rebuildable)

A project commits only `<spec>.lab.lock` (hashes); the bytes live here and move between machines
with `nix-lab push|pull`.
"""

from __future__ import annotations

import os
from pathlib import Path


def store_root() -> Path:
    explicit = os.environ.get("NIX_LAB_STORE")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "nix-lab"


def runs_dir() -> Path:
    return store_root() / "runs"


def data_dir() -> Path:
    return store_root() / "data"


def analysis_dir() -> Path:
    return store_root() / "analysis"
