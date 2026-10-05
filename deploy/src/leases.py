"""Leases that outlive one process: acquire once, use from many commands, release when done.

nixsci deploy lease acquire g5k warm --hosts 4 --walltime 240
nixsci deploy --lease warm run . serve run1
nixsci deploy lease release warm
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from . import providers

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def root() -> Path:
    return (
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        / "nix-deploy"
        / "leases"
    )


def _path(name: str) -> Path:
    if not _NAME.fullmatch(name):
        raise ValueError(f"invalid lease name {name!r}")
    return root() / f"{name}.json"


def acquire(
    name: str,
    provider: str,
    resources: Mapping[str, Any] | None = None,
    opts: Mapping[str, Any] | None = None,
) -> providers.Lease:
    path = _path(name)
    if path.exists():
        raise FileExistsError(f"lease {name!r} already exists; release it first")
    lease = providers.acquire(provider, resources, opts)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "provider": provider,
            "targets": lease.targets,
            "hosts": lease.hosts,
            "state": lease.state,
            "expires": lease.expires,
        }
        path.write_text(json.dumps(record, indent=1) + "\n")
        path.chmod(0o600)
    except BaseException:
        lease.release()
        raise
    return lease


def load(name: str) -> providers.Lease:
    path = _path(name)
    if not path.exists():
        raise FileNotFoundError(f"no lease named {name!r}")
    record = json.loads(path.read_text())
    provider, _ = providers.get(record["provider"])
    return providers.Lease(
        record["targets"],
        record["hosts"],
        record["state"],
        lambda: provider.release(record["state"]),
        record.get("expires"),
    )


def release(name: str) -> None:
    load(name).release()
    _path(name).unlink()


def names() -> list[str]:
    return sorted(p.stem for p in root().glob("*.json")) if root().is_dir() else []
