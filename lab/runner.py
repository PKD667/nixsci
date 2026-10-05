"""Resolve once, fan jobs out over leased hosts, collect records.

The record side of nixsci.lab (`from nixsci import lab`, loading and querying runs) imports none of this.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import hashlib

from nixsci.lab.run import seal

from . import store
from .spec import Spec


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deploy():
    from nixsci.deploy import factory, providers, resolver

    return factory, providers, resolver


def run(
    spec: Spec,
    out_root: Path,
    *,
    poll: float = 1.0,
    timeout: float = 24 * 3600,
    again: bool = False,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Run what is missing and return the manifests of the new runs.

    A run is identified by its inputs (store.input_id); inputs that already have
    `spec.replicates` finished runs are skipped, `again=True` adds one more replicate.
    """
    factory, providers, resolver = _deploy()
    lease = providers.acquire(spec.provider, spec.resources, spec.resources.get("opts", {}))
    try:
        configs = {f"t{i}": c for i, c in enumerate(lease.targets)}
        names = list(configs)
        closures: dict[str, Any] = {}
        for system in {c["system"] for c in configs.values() if "system" in c}:
            log(f"resolving {spec.flake}#{spec.attr} for {system}")
            # a *.lab.lock in the repository is this tool's output, not part of the code
            closures[system] = resolver.resolve(
                spec.flake, spec.attr, system, ignore=("*.lab.lock",)
            )

        def assign(job):
            name = names[job.index % len(names)]
            return name, closures[configs[name]["system"]].path

        todo, satisfied = store.plan(spec, out_root, assign, again=again)
        if satisfied:
            log(f"{satisfied} input(s) already have {spec.replicates} finished run(s): skipped")
        if not todo:
            return []

        def one(item) -> dict[str, Any]:
            job, name, ident, replicate = item
            closure = closures[configs[name]["system"]]
            return _run_job(
                spec,
                job,
                name,
                ident,
                replicate,
                closure,
                configs,
                factory,
                out_root,
                poll,
                timeout,
                log,
                lease.expires,
            )

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            return list(pool.map(one, todo))
    finally:
        lease.release()


def _run_job(
    spec,
    job,
    name,
    ident,
    replicate,
    closure,
    configs,
    factory,
    out_root,
    poll,
    timeout,
    log,
    deadline=None,
):
    run_id = f"{spec.name}-{ident[:12]}-r{replicate}"
    be = factory.backend(name, configs)
    env = {"NIX_LAB_RUN": run_id, "NIX_LAB_PARAMS": json.dumps(job.params, sort_keys=True)}
    if job.seed is not None:
        env["NIX_LAB_SEED"] = str(job.seed)
    if spec.data:
        env["NIX_LAB_SCHEMA"] = json.dumps(spec.data, sort_keys=True)
    if spec.keys:
        env["NIX_LAB_KEYS"] = json.dumps(spec.keys, sort_keys=True)
    started = _utc()
    be.stage(closure)
    if deadline:
        env["NIX_DEPLOY_DEADLINE"] = str(int(deadline))
        timeout = min(timeout, max(0.0, deadline - time.time()))
    handle = be.launch(closure, run_id=run_id, env=env)
    log(f"[{run_id}] launched on {name}")
    give_up = time.monotonic() + timeout
    while be.alive(handle):
        if time.monotonic() > give_up:
            be.stop(handle)
            break
        time.sleep(poll)
    dest = out_root / spec.name / run_id
    dest.mkdir(parents=True, exist_ok=True)
    if be.exists(handle, "lab"):
        be.fetch(handle, "lab", dest / "lab")
    status = json.loads(be.read_file(handle, "lab/status.json") or "null")
    state = "incomplete" if status is None else ("ok" if status["exit_code"] == 0 else "failed")
    manifest = {
        "app": spec.name,
        "schema": spec.data,
        "keys": spec.keys,
        "run": run_id,
        "state": state,
        "seed": job.seed,
        "params": job.params,
        "status": status,
        "target": name,
        "started": started,
        "ended": _utc(),
        "source": closure.source,
        "closure": closure.path,
        "spec": spec.path.name,
        "input_id": ident,
        "replicate": replicate,
        "tolerance": spec.tolerance,
    }
    spec_bytes = spec.path.read_bytes()
    (dest / "spec.toml").write_bytes(spec_bytes)
    manifest["spec_sha256"] = hashlib.sha256(spec_bytes).hexdigest()
    seal(dest, manifest, local=False)
    (dest / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=1, default=str) + "\n"
    )
    if state == "ok":
        be.remove(handle)
    log(f"[{run_id}] {state} -> {dest}")
    return manifest
