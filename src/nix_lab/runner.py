"""Resolve once, fan jobs out over leased hosts, collect records.

Needs the optional `nix-deploy` package (`pip install nix-lab[deploy]`); the
record side of nix-lab (`import lab`, loading and querying runs) does not.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .spec import Job, Spec


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deploy():
    try:
        from nix_deploy import factory, providers, resolver
    except ImportError as error:
        raise SystemExit("running experiments needs nix-deploy: install nix-lab[deploy]") from error
    return factory, providers, resolver


def run(
    spec: Spec,
    out_root: Path,
    *,
    poll: float = 1.0,
    timeout: float = 24 * 3600,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    factory, providers, resolver = _deploy()
    jobs = spec.jobs()
    lease = providers.acquire(spec.provider, spec.resources, spec.resources.get("opts", {}))
    try:
        configs = {f"t{i}": c for i, c in enumerate(lease.targets)}
        names = list(configs)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        closures: dict[str, Any] = {}
        for system in {c["system"] for c in configs.values() if "system" in c}:
            log(f"resolving {spec.flake}#{spec.attr} for {system}")
            closures[system] = resolver.resolve(spec.flake, spec.attr, system)

        def one(job: Job) -> dict[str, Any]:
            name = names[job.index % len(names)]
            return _run_job(
                spec, job, name, configs, closures, factory, out_root, stamp, poll, timeout, log
            )

        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            return list(pool.map(one, jobs))
    finally:
        lease.release()


def _run_job(spec, job, name, configs, closures, factory, out_root, stamp, poll, timeout, log):
    seedpart = f"-s{job.seed}" if job.seed is not None else ""
    run_id = f"{spec.name}-{stamp}-{job.index:03d}{seedpart}"
    be = factory.backend(name, configs)
    closure = closures[configs[name]["system"]]
    env = {"NIX_LAB_RUN": run_id, "NIX_LAB_PARAMS": json.dumps(job.params, sort_keys=True)}
    if job.seed is not None:
        env["NIX_LAB_SEED"] = str(job.seed)
    if spec.data:
        env["NIX_LAB_SCHEMA"] = json.dumps(spec.data, sort_keys=True)
    if spec.keys:
        env["NIX_LAB_KEYS"] = json.dumps(spec.keys, sort_keys=True)
    started = _utc()
    be.stage(closure)
    handle = be.launch(closure, run_id=run_id, env=env)
    log(f"[{run_id}] launched on {name}")
    deadline = time.monotonic() + timeout
    while be.alive(handle):
        if time.monotonic() > deadline:
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
        "v": 1,
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
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=1, default=str) + "\n"
    )
    if state == "ok":
        be.remove(handle)
    log(f"[{run_id}] {state} -> {dest}")
    return manifest
