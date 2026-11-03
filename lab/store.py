"""Run identity: what a run *is*, independent of when or where it ran.

A run's `input_id` is a hash of everything that determines what it computes: the experiment
closure (code and every dependency, by store path), the parameters, the seed, the declared
dataset schema and keys, and the `span` when the experiment declares one. Two runs with the same
`input_id` are replicates of the same measurement. That makes the executor idempotent and resumable:
inputs that already have enough finished replicates are skipped, and a changed line of code (a new
closure) or a changed schema is a new input.

This is the one definition of "the same measurement". lab/nix/lab.nix computes the same hash over the
same canonical JSON, to tell a locked run of the current experiment from one that is not; the two are
checked against each other in tests/test_identity_nix.py.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


def input_id(
    app: str,
    closure: str,
    params: Mapping[str, Any],
    seed: int | None,
    schema: Mapping[str, Any],
    keys: Mapping[str, Any],
    span: int = 1,
) -> str:
    body: dict[str, Any] = {
        "app": app,
        "closure": closure,
        "params": params,
        "seed": seed,
        "schema": schema,
        "keys": keys,
    }
    if span != 1:  # as in the spec: only when it differs from 1, so an ordinary job keeps its id
        body["span"] = span
    # Not ASCII-escaped: Nix's toJSON writes UTF-8 as it is, and lab.nix hashes the same bytes.
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def job_id(spec: Any, closure: str, job: Any) -> str:
    """The input_id of `job` of `spec` run from `closure`."""
    return input_id(spec.name, closure, job.params, job.seed, spec.data, spec.keys, spec.span)


def current_ids(spec: Any, closures: Iterable[str]) -> set[str]:
    """Every input_id the experiment as it is now can produce: each job, from each of its closures."""
    jobs = spec.jobs()
    return {job_id(spec, closure, job) for closure in closures for job in jobs}


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


def _by_input(runs: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for run in runs:
        if run.get("input_id"):
            grouped[run["input_id"]].append(run)
    return grouped


def plan(
    spec: Any,
    root: str | Path,
    assign: Callable[[Any], tuple[str, str]],
    *,
    again: bool = False,
    locked: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[tuple[Any, str, str, int]], int]:
    """What still has to run -> ([(job, target, input_id, replicate)], already_satisfied).

    `assign(job)` says which target the job would run on and with which closure path. Each input
    needs `spec.replicates` finished (`ok`) runs; `again=True` asks for one more regardless.

    `locked` is the facts of the runs the project's lab.lock lists for this experiment. When given,
    those are the runs that count: the lock is what an analysis is built from, so an input is
    satisfied by a locked run whichever machine measured it, and a run only staged here (a verify
    replicate, a run whose publish failed) does not count. Without it, the runs staged under `root`
    count. Replicate numbers continue after every run either one knows, whatever its state, so a name
    is never given to two different measurements.
    """
    staged = _by_input(m for group in index(root, spec.name).values() for _, m in group)
    in_lock = _by_input(locked or ())
    counted = staged if locked is None else in_lock
    planned: dict[str, int] = defaultdict(int)
    todo: list[tuple[Any, str, str, int]] = []
    satisfied = 0
    for job in spec.jobs():
        target, closure = assign(job)
        ident = job_id(spec, closure, job)
        ok = sum(1 for m in counted.get(ident, []) if m.get("state") == "ok")
        need = 1 if again else max(0, spec.replicates - ok)
        if need == 0:
            satisfied += 1
        known = [*staged.get(ident, []), *in_lock.get(ident, [])]
        last = max([len(staged.get(ident, [])), *(m.get("replicate") or 0 for m in known)])
        for _ in range(need):
            planned[ident] += 1
            todo.append((job, target, ident, last + planned[ident]))
    return todo, satisfied
