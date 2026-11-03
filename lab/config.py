"""Machine-local configuration: JSON files, never environment variables.

A setting that belongs to a machine (where the shared input store is, which peers to sync with) is a
file named `<name>.json` in `/etc/nixsci/` or in the user's config directory (`~/.config/nixsci/`, or
under $XDG_CONFIG_HOME when the system sets that), the user's file after the system's. Nobody has to
export anything to run an experiment, and a setting is in a file that can be read, committed to a
dotfiles repository, or copied to the next machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SYSTEM = Path("/etc/nixsci")


def user_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "nixsci"


def files(name: str) -> list[Path]:
    """The `<name>.json` files that exist, in the order they apply: the system's, then the user's."""
    return [d / f"{name}.json" for d in (SYSTEM, user_dir()) if (d / f"{name}.json").is_file()]


def setting(name: str, key: str, default: Any = None) -> Any:
    """`key` of `<name>.json`; the user's file wins over the system's."""
    value = default
    for path in files(name):
        body = json.loads(path.read_text())
        if not isinstance(body, dict):
            raise ValueError(f"{path}: expected a JSON object")
        value = body.get(key, value)
    return value
