"""Providers turn "I need N hosts" into backends, and give them back afterwards.

A provider never runs experiments; it only acquires and releases machines. What
a provider name means is decided per machine, so callers (nerve, nix-lab) only
ever say `provider = "g5k"`:

    # ~/.config/nix-deploy/providers.toml
    [providers.g5k]
    use = "oar"            # direct: your own ssh keys to the OAR frontend
    login = "me"
    site = "lille"
    # or, on a server with a shared-credential helper:
    # use = "site-helper"  # any provider installed under `nix_deploy.providers`
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


@dataclass
class Lease:
    """What `acquire` returns. `targets` are `factory.backend` configs, one per host."""
    targets: list[dict[str, Any]]
    hosts: list[str] = field(default_factory=list)
    _release: Callable[[], None] = lambda: None

    def release(self) -> None:
        self._release()


@dataclass(frozen=True)
class Resources:
    """What every provider understands. Anything else is a provider option."""
    hosts: int = 1
    gpus: int = 0
    walltime: int = 60          # minutes
    system: str = "x86_64-linux"

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> "Resources":
        unknown = set(raw) - {f for f in cls.__dataclass_fields__} - {"provider", "opts"}
        if unknown:
            raise ValueError(f"unknown resources {sorted(unknown)}; provider-specific settings go in [resources.opts]")
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def check_opts(name: str, opts: Mapping[str, Any], required: set[str], optional: set[str]) -> dict[str, Any]:
    missing, unknown = required - set(opts), set(opts) - required - optional
    if missing or unknown:
        raise ValueError(f"provider {name!r} options: missing {sorted(missing)}, unknown {sorted(unknown)}")
    return dict(opts)


class Provider(Protocol):
    def acquire(self, resources: Resources, opts: Mapping[str, Any]) -> Lease: ...


def system() -> str:
    return {"x86_64": "x86_64-linux", "aarch64": "aarch64-linux"}.get(platform.machine(), "x86_64-linux")


class Local:
    """This machine's own Nix store; no privileges, no ssh."""

    def acquire(self, resources, opts):
        check_opts("local", opts, set(), set())
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "nix-deploy" / "runs"
        return Lease([{"backend": "native", "system": system(), "store": "/nix/store",
                       "run_root": str(state / f"slot{i}"), "rootless": False} for i in range(resources.hosts)])


class Static:
    """Named targets from a targets file (CBP servers, any ssh host)."""

    def acquire(self, resources, opts):
        o = check_opts("static", opts, {"targets"}, {"config"})
        config = Path(o.get("config", "~/.config/nix-deploy/targets.toml")).expanduser()
        table = load_toml(config)["targets"]
        names = list(o["targets"])
        if len(names) < resources.hosts:
            raise ValueError(f"asked for {resources.hosts} hosts, static provider lists {len(names)}")
        names = names[:resources.hosts]
        missing = [n for n in names if n not in table]
        if missing:
            raise KeyError(f"targets not in {config}: {missing}")
        return Lease([dict(table[n]) for n in names], list(names))


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _ssh(argv: list[str], command: str, check: bool = True) -> str:
    result = subprocess.run([*argv, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and result.returncode:
        raise RuntimeError(f"ssh failed ({result.returncode}): {command}\n{result.stderr[-1500:]}")
    return result.stdout


class OAR:
    """Reserve nodes on an OAR cluster (Grid'5000) over plain ssh and your own keys.

    Reaches the frontend as `login@site` through `access` (ProxyJump) and each
    node the same way, so nothing but ssh keys is needed on the calling side.
    Nodes are plain `ssh` backends with a pinned static Nix bootstrap.
    """

    def acquire(self, resources, opts):
        c = {"access": "access.grid5000.fr", "queue": None, "cluster": None, "besteffort": False,
             "poll": 2, "timeout": 3600,
             **check_opts("oar", opts, {"login", "site", "bootstrap", "bootstrap_sha256"},
                          {"access", "queue", "cluster", "besteffort", "poll", "timeout",
                           "store", "run_root", "remote_bootstrap"})}
        login, site, access = c["login"], c["site"], c["access"]
        for value in (login, site, access, c["cluster"] or "x", c["queue"] or "x"):
            if not _NAME.fullmatch(value):
                raise ValueError(f"invalid OAR option {value!r}")
        walltime, hosts = resources.walltime, resources.hosts
        jump = ["-o", f"ProxyJump={login}@{access}"]
        frontend = ["ssh", "-o", "BatchMode=yes", *jump, f"{login}@{site}"]
        cmd = f"oarsub -n nix-deploy -l nodes={hosts},walltime={walltime // 60}:{walltime % 60:02d}:00"
        if c["besteffort"]:
            cmd += " -t besteffort"
        if c["queue"]:
            cmd += f" -q {c['queue']}"
        if c["cluster"]:
            cmd += f" -p \"cluster='{c['cluster']}'\""
        out = _ssh(frontend, f"{cmd} 'sleep 2147483647'")
        found = re.search(r"^OAR_JOB_ID=(\d+)", out, re.M)
        if not found:
            raise RuntimeError(f"no OAR job id in: {out!r}")
        job = found.group(1)

        def release() -> None:
            _ssh(frontend, f"oardel {job}", check=False)

        try:
            deadline = time.monotonic() + float(c["timeout"])
            while True:
                info = _ssh(frontend, f"oarstat -fj {job}")
                state = re.search(r"^\s*state = (\w+)", info, re.M)
                state = state.group(1) if state else ""
                if state == "Running":
                    break
                if state in ("", "Terminated", "Error", "Finishing") or time.monotonic() > deadline:
                    raise RuntimeError(f"OAR job {job} did not run: state={state or 'unknown'}")
                time.sleep(float(c["poll"]))
            names = re.search(r"^\s*assigned_hostnames = (.+)$", info, re.M)
            nodes = sorted(set(names.group(1).strip().split("+"))) if names else []
            if not nodes or not all(_NAME.fullmatch(n) for n in nodes):
                raise RuntimeError(f"OAR job {job} has no usable assigned hosts: {nodes!r}")
        except BaseException:
            release()
            raise
        base = {"backend": "ssh", "system": resources.system,
                "store": c.get("store", f"/tmp/{login}-nix-deploy/store"),
                "run_root": c.get("run_root", f"/tmp/{login}-nix-deploy/runs"), "rootless": True,
                "bootstrap": c["bootstrap"], "bootstrap_sha256": c["bootstrap_sha256"],
                "remote_bootstrap": c.get("remote_bootstrap", f"/tmp/{login}-nix-deploy/bin/nix"),
                "ssh_options": jump}
        return Lease([{**base, "host": f"{login}@{node}"} for node in nodes], nodes, release)


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


_BUILTIN: dict[str, Callable[[], Provider]] = {"local": Local, "static": Static, "oar": OAR}


def get(name: str, config_path: str | Path = "~/.config/nix-deploy/providers.toml") -> tuple[Provider, dict[str, Any]]:
    """Resolve a logical provider name on this machine -> (provider, its default options)."""
    path = Path(config_path).expanduser()
    options: dict[str, Any] = {}
    if path.exists():
        options = dict(load_toml(path).get("providers", {}).get(name, {}))
    use = options.pop("use", name)
    if use in _BUILTIN:
        return _BUILTIN[use](), options
    for ep in entry_points(group="nix_deploy.providers"):
        if ep.name == use:
            return ep.load()(), options
    available = sorted({*_BUILTIN, *(e.name for e in entry_points(group="nix_deploy.providers"))})
    raise KeyError(f"provider {use!r} (for {name!r}) not found; available: {available}")


def acquire(name: str, resources: Mapping[str, Any] | None = None, opts: Mapping[str, Any] | None = None,
            **kw: Any) -> Lease:
    """`resources` is generic; `opts` is for the provider alone (machine defaults fill gaps)."""
    provider, defaults = get(name, **kw)
    return provider.acquire(Resources.of(resources or {}), {**defaults, **(opts or {})})


__all__ = ["Lease", "Provider", "Resources", "acquire", "get", "Local", "Static", "OAR"]
