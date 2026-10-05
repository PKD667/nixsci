from __future__ import annotations
import re
from .backend import normalize_manifest
from .model import Closure
from . import nix

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SYSTEMS = {"x86_64-linux", "aarch64-linux"}


def resolve(flake: str, experiment: str, system: str) -> Closure:
    if not isinstance(flake, str) or not flake or "\x00" in flake:
        raise ValueError("flake reference is required")
    if not isinstance(experiment, str) or not _NAME.fullmatch(experiment):
        raise ValueError("experiment must be a derivation name")
    if system not in _SYSTEMS:
        raise ValueError(f"unsupported target system {system!r}")
    source, snapshot = nix.capture_source("nix", flake)
    installable = (
        experiment
        if experiment.startswith(("experiments.", "packages.", "apps."))
        else f"experiments.{system}.{experiment}"
    )
    root = nix.build("nix", snapshot, installable, system)
    hashes = nix.verify("nix", None, root)
    metadata = normalize_manifest(nix.manifest("nix", root), hashes)
    return Closure(root, metadata["program"], metadata, hashes, source, system)


__all__ = ["resolve"]
