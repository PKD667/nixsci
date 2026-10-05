"""Run identity: what a run *is*, independent of when or where it ran.

A run's `input_id` is a hash of everything that determines what it computes: the experiment
closure (code and every dependency, by store path), the parameters, the seed, and the declared
dataset schema and keys. Two runs with the same `input_id` are replicates of the same
measurement. That makes `nixsci lab run` idempotent and resumable: inputs that already have enough
finished replicates are skipped, and a changed line of code (a new closure) is a new input.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping


def input_id(
    app: str,
    closure: str,
    params: Mapping[str, Any],
    seed: int | None,
    schema: Mapping[str, Any],
    keys: Mapping[str, Any],
) -> str:
    body = {
        "app": app,
        "closure": closure,
        "params": params,
        "seed": seed,
        "schema": schema,
        "keys": keys,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def index(root: str | Path, app: str) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    """Every run of `app` under `root`, grouped by input_id (runs without one are ignored)."""
    found: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    for path in sorted((Path(root) / app).glob("*/manifest.json")):
        try:
            manifest = json.loads(path.read_text())
        except ValueError:
            continue
        if manifest.get("input_id"):
            found[manifest["input_id"]].append((path.parent, manifest))
    return found


def plan(
    spec: Any,
    root: str | Path,
    assign: Callable[[Any], tuple[str, str]],
    *,
    again: bool = False,
) -> tuple[list[tuple[Any, str, str, int]], int]:
    """What still has to run -> ([(job, target, input_id, replicate)], already_satisfied).

    `assign(job)` says which target the job would run on and with which closure path. Each
    input needs `spec.replicates` finished (`ok`) runs; `again=True` asks for one more
    regardless. Replicate numbers continue after the runs already on disk, whatever their state.
    """
    existing = index(root, spec.name)
    planned: dict[str, int] = defaultdict(int)
    todo: list[tuple[Any, str, str, int]] = []
    satisfied = 0
    for job in spec.jobs():
        target, closure = assign(job)
        ident = input_id(spec.name, closure, job.params, job.seed, spec.data, spec.keys)
        have = existing.get(ident, [])
        ok = sum(1 for _, m in have if m.get("state") == "ok")
        need = 1 if again else max(0, spec.replicates - ok)
        if need == 0:
            satisfied += 1
        for _ in range(need):
            planned[ident] += 1
            todo.append((job, target, ident, len(have) + planned[ident]))
    return todo, satisfied
