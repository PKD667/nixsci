"""Backend selection. Built-ins plus anything installed under `nixsci.deploy.backends`."""

from importlib.metadata import entry_points
from pathlib import Path
import tomllib


def load_targets(path):
    targets = tomllib.loads(Path(path).expanduser().read_text())["targets"]
    if not isinstance(targets, dict):
        raise ValueError("deployment configuration must contain a [targets] table")
    return targets


def _checked(name, kind, config):
    required = {"system", "store", "run_root", "rootless"}
    if kind == "ssh":
        required |= {"host", "remote_bootstrap"}
    missing = required - config.keys()
    if missing:
        raise ValueError(f"target {name!r} is missing {', '.join(sorted(missing))}")
    return config


def backend(name, configs):
    if name not in configs:
        raise KeyError(f"target {name!r} is not configured")
    config = dict(configs[name])
    kind = config.pop("backend")
    if kind == "native":
        from .native import Native

        return Native(**_checked(name, kind, config))
    if kind == "ssh":
        from .ssh import SSH

        return SSH(**_checked(name, kind, config))
    for ep in entry_points(group="nixsci.deploy.backends"):
        if ep.name == kind:
            return ep.load()(config)
    raise ValueError(f"target {name!r}: unknown backend {kind!r}")
