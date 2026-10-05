"""Durable JSON handles for the deploy CLI."""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any, Mapping
def write(path: str | Path, handle: Mapping[str, Any]) -> Path:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(dict(handle), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"); os.chmod(target, 0o600); return target
def read(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("closure"), dict): raise ValueError("handle JSON must be an object containing closure")
    return value
__all__ = ["read", "write"]
