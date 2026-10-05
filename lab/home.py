"""Where nixsci.lab keeps its files.

Outputs belong to a project and are not shared. A project is the nearest directory, from the spec
or the working directory upwards, that holds a `.git`, or else the directory of the spec:

    <project>/.nixsci/
        runs/<app>/<run>/        immutable, sealed runs (what `lab.Run` and `nixsci lab run` write)
        data/<app>/<dataset>/    Parquet derived from the runs (rebuildable)
        analysis/<app>/<name>/   pipeline outputs plus provenance.json (rebuildable)

The directory ignores itself in git. A project commits only `<spec>.lab.lock` (hashes); the bytes
move between machines with `nixsci lab push|pull`. `$NIXSCI_STORE` puts these directories
somewhere else.

Inputs (datasets and models) are meant to be shared and live in a separate input store, see
`inputs_root`.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root(near: str | os.PathLike[str] | None = None) -> Path:
    start = Path(near).resolve() if near is not None else Path.cwd()
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return start


def store_root(near: str | os.PathLike[str] | None = None) -> Path:
    """The outputs store of the project that holds `near` (default: the working directory)."""
    explicit = os.environ.get("NIXSCI_STORE")
    if explicit:
        return Path(explicit).expanduser()
    root = project_root(near) / ".nixsci"
    if not root.exists():
        root.mkdir(parents=True)
        (root / ".gitignore").write_text("*\n")
    return root


def inputs_root() -> Path:
    """The input store (datasets and models): `$NIXSCI_INPUTS`, else `~/.nixsci`.

    It is meant to be shared, so a grid whose storage is shared by its nodes sets it to a
    directory there.
    """
    explicit = os.environ.get("NIXSCI_INPUTS")
    return Path(explicit).expanduser() if explicit else Path.home() / ".nixsci"


def runs_dir(near: str | os.PathLike[str] | None = None) -> Path:
    return store_root(near) / "runs"


def data_dir(near: str | os.PathLike[str] | None = None) -> Path:
    return store_root(near) / "data"


def analysis_dir(near: str | os.PathLike[str] | None = None) -> Path:
    return store_root(near) / "analysis"
