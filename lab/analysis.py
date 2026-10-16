"""Python analysis binding for the same Parquet view used by R.

    from nixsci.lab.analysis import use, out, values
    data = use("demo")
    rows = data.table("loss").to_table().to_pylist()
    values({"loss": rows[-1]["value"]})  # writes $NIXSCI_OUT/values.json

The view contains only aliases in the analysis's `use` declaration. No recorder dependency.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _root(new: str, old: str) -> Path:
    value = os.environ.get(new) or os.environ.get(old)
    if not value:
        raise RuntimeError(f"not inside a nixsci analysis: {new} is not set")
    return Path(value)


def out(*parts: str) -> Path:
    root = _root("NIXSCI_OUT", "NIX_LAB_OUT")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("analysis output must stay inside NIXSCI_OUT")
    return path


class View:
    def __init__(self, alias: str):
        root = _root("NIXSCI_VIEW", "NIX_LAB_VIEW")
        if alias not in [p.name for p in root.iterdir() if p.is_dir()]:
            raise ValueError(f"alias {alias!r} is not declared in `use`")
        self.root = root / alias

    def table(self, name: str):
        import pyarrow.dataset as ds
        if name not in [p.name for p in self.root.iterdir() if p.is_dir()]:
            raise ValueError(f"no dataset {name!r} in {self.root.name}")
        return ds.dataset(self.root / name, format="parquet")

    def runs(self) -> list[dict]:
        return json.loads((self.root / "runs.json").read_text())

    def code(self) -> dict:
        return json.loads((self.root / "code.json").read_text())


def use(alias: str) -> View:
    return View(alias)


def values(data: dict, name: str = "values.json") -> Path:
    path = out(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, allow_nan=False, sort_keys=True, indent=1) + "\n")
    return path
