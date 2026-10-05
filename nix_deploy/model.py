from __future__ import annotations
from dataclasses import dataclass
import json, re
from types import MappingProxyType
from typing import Any, Mapping

_STORE = re.compile(r"^/nix/store/[0-9a-z]{32}-[^/]+$")
_HASH = re.compile(r"^(?:sha256|sha1|sha512)-[A-Za-z0-9+/=]+$")
_SYSTEMS = {"x86_64-linux", "aarch64-linux"}


def _store_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not _STORE.fullmatch(value):
        raise ValueError(f"{field} must be a logical Nix store path")
    return value


def _store_file(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/nix/store/") or "\x00" in value:
        raise ValueError(f"{field} must be an absolute Nix store executable")
    root = "/".join(value.split("/")[:4])
    _store_path(root, field)
    if value == root or any(part in ("", ".", "..") for part in value[len(root) + 1 :].split("/")):
        raise ValueError(f"{field} must name a normalized file below its store path")
    return value


@dataclass(frozen=True)
class Closure:
    """Resolved experiment manifest and the complete NAR-identified closure."""

    path: str
    program: str
    metadata: Mapping[str, Any]
    closure: Mapping[str, str]
    source: Mapping[str, Any] | str
    system: str

    def __post_init__(self) -> None:
        _store_path(self.path, "closure.path")
        _store_file(self.program, "closure.program")
        if self.system not in _SYSTEMS:
            raise ValueError(f"unsupported target system {self.system!r}")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("closure.metadata must be an object")
        commands, inputs, nested = (
            self.metadata.get("commands", {}),
            self.metadata.get("inputs", {}),
            self.metadata.get("metadata", {}),
        )
        if (
            not isinstance(commands, Mapping)
            or not isinstance(inputs, Mapping)
            or not isinstance(nested, Mapping)
        ):
            raise TypeError("closure metadata commands, inputs, and metadata must be objects")
        for name, item in commands.items():
            if not isinstance(name, str) or not isinstance(item, str):
                raise TypeError("closure command names and paths must be strings")
            _store_file(item, f"closure.commands.{name}")
        if not isinstance(self.closure, Mapping) or not self.closure:
            raise ValueError("closure.closure must contain every Nix path")
        paths = set(self.closure)
        if self.path not in paths:
            raise ValueError("closure.closure must contain closure.path")
        for path, nar_hash in self.closure.items():
            _store_path(path, "closure.closure path")
            if not isinstance(nar_hash, str) or not _HASH.fullmatch(nar_hash):
                raise ValueError(f"invalid NAR hash for {path}")
        for name, item in commands.items():
            if not any(item == p or item.startswith(p + "/") for p in paths):
                raise ValueError(f"closure.commands.{name} is outside the complete closure")
        if not any(self.program == p or self.program.startswith(p + "/") for p in paths):
            raise ValueError("closure.program is not in the complete closure")
        if not isinstance(self.source, (str, Mapping)):
            raise TypeError("closure.source must be a source identity")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(self, "closure", MappingProxyType(dict(self.closure)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "program": self.program,
            "metadata": dict(self.metadata),
            "closure": dict(self.closure),
            "source": self.source,
            "system": self.system,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Closure":
        if not isinstance(value, Mapping):
            raise TypeError("closure handle must contain an object")
        return cls(
            value["path"],
            value["program"],
            value["metadata"],
            value["closure"],
            value["source"],
            value["system"],
        )

    def json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


__all__ = ["Closure"]
