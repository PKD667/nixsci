"""Immutable Nix closure deployment with explicit private run staging."""

from .model import Closure
from .resolver import resolve
from .native import Native
from .ssh import SSH
from .backend import normalize_manifest
from .factory import backend

__all__ = ["Closure", "Native", "SSH", "backend", "normalize_manifest", "resolve"]
