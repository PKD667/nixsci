"""Move runs and outputs between stores over plain ssh (rsync). No server, no central copy.

A *remote* is any directory with the store layout, local or `host:/path`. Runs are immutable and
named by identity, so they are copied once (`--ignore-existing`); everything is verified by hash
against the lock afterwards, so a wrong or partial copy cannot pass for the real one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from . import lock


def _rsync(source: str, dest: str, *, replace: bool, log: Callable[[str], None]) -> bool:
    cmd = ["rsync", "-a", "--mkpath", "-e", "ssh -o BatchMode=yes"]
    if not replace:
        cmd.append("--ignore-existing")
    cmd += [source.rstrip("/") + "/", dest.rstrip("/") + "/"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        log(f"  rsync failed: {result.stderr.strip()[-200:]}")
    return result.returncode == 0


def _targets(data: dict[str, Any]):
    for entry in data["run"]:
        yield "runs", f"{entry['app']}/{entry['run']}", False
    for pipeline in data["pipeline"]:
        yield "analysis", f"{data['app']}/{pipeline['name']}", True


def pull(
    remote: str, data: dict[str, Any], store: Path, log: Callable[[str], None] = print
) -> list[tuple[str, str, str]]:
    """Fetch what the lock names and the local store lacks or has wrong; return remaining problems."""
    problems = lock.check(data, store / "runs", store / "analysis" / data["app"])
    wanted = {what.split("/")[0] for _, what, _ in problems}
    for kind, rel, replace in _targets(data):
        name = rel.split("/")[-1]
        if name not in wanted:
            continue
        log(f"pull {kind}/{rel}")
        _rsync(
            f"{remote.rstrip('/')}/{kind}/{rel}", str(store / kind / rel), replace=replace, log=log
        )
    return lock.check(data, store / "runs", store / "analysis" / data["app"])


def push(remote: str, data: dict[str, Any], store: Path, log: Callable[[str], None] = print) -> int:
    """Send the runs and outputs the lock names to `remote`; returns the number that failed."""
    failed = 0
    for kind, rel, replace in _targets(data):
        source = store / kind / rel
        if not source.is_dir():
            log(f"skip {kind}/{rel}: not in the local store")
            failed += 1
            continue
        log(f"push {kind}/{rel}")
        if not _rsync(str(source), f"{remote.rstrip('/')}/{kind}/{rel}", replace=replace, log=log):
            failed += 1
    return failed
