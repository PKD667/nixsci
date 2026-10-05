"""`nix-lab build`: run what is missing, compact, analyse, lock. No directories to name.

Every step is incremental, so building twice does nothing the second time: finished inputs are
skipped (store.plan), compacted runs are not rewritten, and a pipeline whose script and input
runs are unchanged is not rerun (analyze fingerprint).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable

from lab import home

from . import lock
from .spec import Spec


def build(spec: Spec, *, again: bool = False, log: Callable[[str], None] = print) -> int:
    from .analyze import analyze
    from .compact import compact
    from .runner import run

    runs_root, data_root = home.runs_dir(), home.data_dir()
    analysis_root = home.analysis_dir() / spec.name
    log(f"store: {home.store_root()}")

    new = run(spec, runs_root, again=again, log=log)
    failed = [m for m in new if m["state"] != "ok"]
    for m in failed:
        log(f"FAILED {m['run']} ({m['state']})")
    if failed:
        return 1
    log(f"{len(new)} new run(s)" if new else "no new runs needed")

    if spec.data:
        written = compact(runs_root, data_root)
        log(f"compacted {len(written)} file(s)" if written else "data up to date")

    if spec.pipelines:
        codes = analyze(spec, runs_root, data_root, analysis_root)
        for name, (code, skipped) in codes.items():
            log(f"pipeline {name}: " + ("up to date" if skipped else f"exit {code}"))
        if any(code for code, _ in codes.values()):
            return 1
        written = lock.write(spec.path, lock.collect(spec, runs_root, analysis_root))
        log(f"lock: {written}")
    return 0


def listing(app: str | None = None, log: Callable[[str], None] = print) -> None:
    """What the store holds: per app, inputs and their replicates by state."""
    import json

    root = home.runs_dir()
    if not root.is_dir():
        log(f"(empty store: {home.store_root()})")
        return
    for app_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if app and app_dir.name != app:
            continue
        inputs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for manifest in app_dir.glob("*/manifest.json"):
            m = json.loads(manifest.read_text())
            inputs[m.get("input_id") or m["run"]][m.get("state", "?")] += 1
        runs = sum(sum(states.values()) for states in inputs.values())
        log(f"{app_dir.name}: {len(inputs)} input(s), {runs} run(s)")
        if app:
            for ident, states in sorted(inputs.items()):
                log(f"  {ident[:12]}  " + ", ".join(f"{n} {s}" for s, n in sorted(states.items())))
