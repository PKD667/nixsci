"""Providers turn "I need N hosts" into backends, and give them back afterwards.

A provider never runs experiments; it only acquires and releases machines. What
a provider name means is decided per machine, so callers (nerve, nix-lab) only
ever say `provider = "g5k"`:

    # ~/.config/nix-deploy/providers.toml
    [providers.lab]
    use = "static"         # hosts you reserved yourself, reached over ssh
    jump = "me@gateway"
    user = "me"
    # or, on a server with a shared-credential helper:
    # use = "site-helper"  # any provider installed under `nixsci.deploy.providers`
"""

from __future__ import annotations

import os
import platform
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
    #: JSON-able facts the provider needs to release this lease from any process.
    state: dict[str, Any] = field(default_factory=dict)
    _release: Callable[[], None] = lambda: None
    #: Epoch seconds after which the hosts are not ours any more (None = unknown).
    expires: float | None = None

    def release(self) -> None:
        self._release()


@dataclass(frozen=True)
class Resources:
    """What every provider understands. Anything else is a provider option."""

    hosts: int = 1
    gpus: int = 0
    walltime: int | None = None  # minutes; None = no limit known
    system: str = "x86_64-linux"

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> "Resources":
        unknown = set(raw) - {f for f in cls.__dataclass_fields__} - {"provider", "opts"}
        if unknown:
            raise ValueError(
                f"unknown resources {sorted(unknown)}; provider-specific settings go in [resources.opts]"
            )
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


def check_opts(
    name: str, opts: Mapping[str, Any], required: set[str], optional: set[str]
) -> dict[str, Any]:
    missing, unknown = required - set(opts), set(opts) - required - optional
    if missing or unknown:
        raise ValueError(
            f"provider {name!r} options: missing {sorted(missing)}, unknown {sorted(unknown)}"
        )
    return dict(opts)


class Provider(Protocol):
    def acquire(self, resources: Resources, opts: Mapping[str, Any]) -> Lease: ...
    def release(self, state: Mapping[str, Any]) -> None: ...


def system() -> str:
    return {"x86_64": "x86_64-linux", "aarch64": "aarch64-linux"}.get(
        platform.machine(), "x86_64-linux"
    )


class Local:
    """This machine's own Nix store; no privileges, no ssh."""

    def release(self, state):
        pass

    def acquire(self, resources, opts):
        check_opts("local", opts, set(), set())
        state = (
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "nix-deploy"
            / "runs"
        )
        return Lease(
            [
                {
                    "backend": "native",
                    "system": system(),
                    "store": "/nix/store",
                    "run_root": str(state / f"slot{i}"),
                    "rootless": False,
                }
                for i in range(resources.hosts)
            ]
        )


class Static:
    """Hosts you already have. Reservations and boots are somebody else's job.

    Either name targets from a targets file:      targets = ["box1", "box2"], config = "path.toml"
    or list ssh hosts sharing one template:       hosts = ["a", "b"] (or "a,b"), and optionally user,
        workdir, jump, ssh_command, scp_command, ssh_options, rsh, ready_timeout, bootstrap.
        `workdir` (default /tmp/<user>-nix-deploy) is an absolute path on the hosts that holds the
        rootless store, the run directories and the shipped Nix; every listed host is used. The
        shipped static Nix is `nixpkgs#nixStatic` unless `bootstrap` names another flake reference
        (or a file, then with its `bootstrap_sha256`).
    """

    def release(self, state):
        pass

    def acquire(self, resources, opts):
        if "hosts" in opts:
            return self._hosts(resources, opts)
        o = check_opts("static", opts, {"targets"}, {"config"})
        config = Path(o.get("config", "~/.config/nix-deploy/targets.toml")).expanduser()
        table = load_toml(config)["targets"]
        names = list(o["targets"])
        if len(names) < resources.hosts:
            raise ValueError(
                f"asked for {resources.hosts} hosts, static provider lists {len(names)}"
            )
        names = names[: resources.hosts]
        missing = [n for n in names if n not in table]
        if missing:
            raise KeyError(f"targets not in {config}: {missing}")
        return Lease([dict(table[n]) for n in names], list(names))

    def _hosts(self, resources, opts):
        o = check_opts(
            "static",
            opts,
            {"hosts"},
            {
                "workdir",
                "bootstrap",
                "bootstrap_sha256",
                "user",
                "jump",
                "ssh_command",
                "scp_command",
                "ssh_options",
                "rsh",
                "ready_timeout",
            },
        )
        hosts = o["hosts"].split(",") if isinstance(o["hosts"], str) else list(o["hosts"])
        hosts = [h.strip() for h in hosts if h.strip()]
        if len(hosts) < resources.hosts:
            raise ValueError(f"asked for {resources.hosts} hosts, {len(hosts)} listed")
        workdir = o.get("workdir") or (f"/tmp/{o['user']}-nix-deploy" if "user" in o else None)
        if not workdir:
            raise ValueError(
                "static hosts need `workdir` (or `user`, giving /tmp/<user>-nix-deploy)"
            )
        workdir = workdir.rstrip("/")
        if not workdir.startswith("/"):
            raise ValueError("workdir must be an absolute path on the hosts")
        base = {
            "backend": "ssh",
            "system": resources.system,
            "store": f"{workdir}/store",
            "run_root": f"{workdir}/runs",
            "rootless": True,
            "remote_bootstrap": f"{workdir}/bin/nix",
        }
        for key in (
            "bootstrap",
            "bootstrap_sha256",
            "jump",
            "ssh_command",
            "scp_command",
            "ssh_options",
            "rsh",
            "ready_timeout",
        ):
            if key in o:
                base[key] = o[key]
        user = f"{o['user']}@" if "user" in o else ""
        expires = time.time() + resources.walltime * 60 if resources.walltime else None
        return Lease([{**base, "host": f"{user}{h}"} for h in hosts], hosts, expires=expires)


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


_BUILTIN: dict[str, Callable[[], Provider]] = {"local": Local, "static": Static}


#: Per-user settings win; machine-wide ones (/etc) fill in for names the user does not set.
CONFIG_FILES = ("~/.config/nix-deploy/providers.toml", "/etc/nix-deploy/providers.toml")


def get(name: str, config_path: str | Path | None = None) -> tuple[Provider, dict[str, Any]]:
    """Resolve a logical provider name on this machine -> (provider, its default options)."""
    options: dict[str, Any] = {}
    for candidate in [config_path] if config_path else CONFIG_FILES:
        path = Path(candidate).expanduser()
        if path.exists():
            entry = load_toml(path).get("providers", {}).get(name)
            if entry is not None:
                options = dict(entry)
                break
    use = options.pop("use", name)
    if use in _BUILTIN:
        return _BUILTIN[use](), options
    for ep in entry_points(group="nixsci.deploy.providers"):
        if ep.name == use:
            return ep.load()(), options
    available = sorted({*_BUILTIN, *(e.name for e in entry_points(group="nixsci.deploy.providers"))})
    raise KeyError(f"provider {use!r} (for {name!r}) not found; available: {available}")


def acquire(
    name: str,
    resources: Mapping[str, Any] | None = None,
    opts: Mapping[str, Any] | None = None,
    **kw: Any,
) -> Lease:
    """`resources` is generic; `opts` is for the provider alone (machine defaults fill gaps)."""
    provider, defaults = get(name, **kw)
    return provider.acquire(Resources.of(resources or {}), {**defaults, **(opts or {})})


__all__ = ["Lease", "Provider", "Resources", "acquire", "get", "Local", "Static"]
