"""Where nixsci.lab keeps data: one store per machine, never inside a project.

    $NIX_LAB_STORE, else $XDG_DATA_HOME/nix-lab, else ~/.local/share/nix-lab
        runs/<app>/<run>/        immutable, sealed runs (what `lab.Run` and `nixsci lab run` write)
        data/<app>/<dataset>/    Parquet derived from the runs (rebuildable)
        analysis/<app>/<name>/   pipeline outputs plus provenance.json (rebuildable)

A project commits only `<spec>.lab.lock` (hashes); the bytes live here and move between machines
with `nixsci lab push|pull`.
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


def inputs_root() -> Path:
    """The input store (datasets and models): `$NIXSCI_INPUTS`, else `~/.nixsci`.

    It is meant to be shared, so a grid whose storage is shared by its nodes sets it to a
    directory there.
    """
    explicit = os.environ.get("NIXSCI_INPUTS")
    return Path(explicit).expanduser() if explicit else Path.home() / ".nixsci"


def runs_dir() -> Path:
    return store_root() / "runs"


def data_dir() -> Path:
    return store_root() / "data"


def analysis_dir() -> Path:
    return store_root() / "analysis"
