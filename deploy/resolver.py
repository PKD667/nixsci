from __future__ import annotations
import re
from .backend import normalize_manifest
from .model import Closure
from . import nix

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SYSTEMS = {"x86_64-linux", "aarch64-linux"}


def resolve(flake: str, deployment: str, system: str, ignore: tuple[str, ...] = ()) -> Closure:
    if not isinstance(flake, str) or not flake or "\x00" in flake:
        raise ValueError("flake reference is required")
    if not isinstance(deployment, str) or not _NAME.fullmatch(deployment):
        raise ValueError("deployment must be a derivation name")
    if system not in _SYSTEMS:
        raise ValueError(f"unsupported target system {system!r}")
    source, snapshot = nix.capture_source("nix", flake, ignore)
    installable = (
        deployment
        if deployment.startswith(("deployments.", "experiments.", "packages.", "apps."))
        else f"deployments.{system}.{deployment}"
    )
    try:
        root = nix.build("nix", snapshot, installable, system)
    except nix.NixError:
        if deployment.startswith(("deployments.", "experiments.", "packages.", "apps.")):
            raise
        import warnings
        warnings.warn("flake has no deployments output; resolving legacy experiments output", RuntimeWarning)
        root = nix.build("nix", snapshot, f"experiments.{system}.{deployment}", system)
    hashes = nix.verify("nix", None, root)
    metadata = normalize_manifest(nix.manifest("nix", root), hashes)
    return Closure(root, metadata["program"], metadata, hashes, source, system)


def from_path(root: str, system: str, source: dict) -> Closure:
    """A closure from a store path Nix has already built (`source` is the flake's identity)."""
    if system not in _SYSTEMS:
        raise ValueError(f"unsupported target system {system!r}")
    hashes = nix.verify("nix", None, root)
    metadata = normalize_manifest(nix.manifest("nix", root), hashes)
    return Closure(root, metadata["program"], metadata, hashes, source, system)


__all__ = ["resolve", "from_path"]
