"""`<spec>.lab.lock`: the committed claim of which runs and outputs a result rests on.

It holds hashes only. An experiment's lock lists its finished runs (manifest and records hashes,
closure, source, machine). An analysis' lock pins the experiment locks it `use`s by hash, lists the
union of their runs, and for each pipeline its fingerprint and output hashes. A figure therefore
traces to exact runs; anyone can `pull` them from any remote and `check` them by hash.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from nixsci.lab.run import sha256_file

TABLES = ("use", "pipeline", "run")


def path_for(spec_path: Path) -> Path:
    return spec_path.with_name(spec_path.stem + ".lab.lock")


def _entry(manifest: dict[str, Any], manifest_sha256: str, app: str) -> dict[str, Any]:
    return {
        "app": manifest.get("app", app),
        "run": manifest["run"],
        "input_id": manifest.get("input_id"),
        "replicate": manifest.get("replicate"),
        "manifest_sha256": manifest_sha256,
        "records_sha256": manifest.get("records_sha256"),
        "closure": manifest.get("closure"),
        "source": (manifest.get("source") or {}).get("origin"),
        "machine": (manifest.get("machine") or {}).get("hostname"),
    }


def _experiment(spec: Any, runs_root: Path) -> dict[str, Any]:
    runs = []
    for path in sorted((runs_root / spec.name).glob("*/manifest.json")):
        manifest = json.loads(path.read_text())
        if manifest.get("state") == "ok":
            runs.append(_entry(manifest, sha256_file(path), spec.name))
    return {"kind": "experiment", "app": spec.name, "use": [], "pipeline": [], "run": runs}


def _analysis(spec: Any, analysis_root: Path) -> dict[str, Any]:
    uses: list[dict[str, Any]] = []
    runs: dict[str, dict[str, Any]] = {}
    for alias, target in sorted(spec.use.items()):
        used = read(target)
        uses.append(
            {
                "alias": alias,
                "spec": os.path.relpath(target, spec.path.parent),
                "lock_sha256": sha256_file(path_for(target)),
            }
        )
        for entry in used["run"]:
            runs[entry["run"]] = entry
    pipelines: list[dict[str, Any]] = []
    for name in sorted(spec.pipelines):
        out = analysis_root / name
        provenance = out / "provenance.json"
        if not provenance.is_file():
            continue
        info = json.loads(provenance.read_text())
        if info.get("exit_code") != 0:
            continue
        pipelines.append(
            {
                "name": name,
                "fingerprint": info.get("fingerprint"),
                "script_sha256": info.get("script_sha256"),
                "outputs": {
                    str(f.relative_to(out)): sha256_file(f)
                    for f in sorted(out.rglob("*"))
                    if f.is_file() and f.name != "provenance.json"
                },
                "runs": sorted({item["run"] for item in info["inputs"]}),
            }
        )
    return {
        "kind": "analysis",
        "app": spec.name,
        "use": uses,
        "pipeline": pipelines,
        "run": sorted(runs.values(), key=lambda r: r["run"]),
    }


def collect(spec: Any, runs_root: Path, analysis_root: Path) -> dict[str, Any]:
    """Build the lock data from the store (an experiment's runs, or an analysis' uses and outputs)."""
    return _analysis(spec, analysis_root) if spec.kind == "analysis" else _experiment(spec, runs_root)


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        items = [
            f"{json.dumps(str(k))} = {_value(x)}" for k, x in sorted(v.items()) if x is not None
        ]
        return "{ " + ", ".join(items) + " }" if items else "{}"
    raise TypeError(f"cannot write {type(v).__name__} to a lock file")


def dumps(data: dict[str, Any]) -> str:
    lines = [
        "# Written by `nixsci lab lock` (and `build`). Commit it: it is the claim of which runs and",
        "# outputs the results rest on. Hashes only; the data lives in the nixsci lab store.",
        f"kind = {json.dumps(data['kind'])}",
        f"app = {json.dumps(data['app'])}",
    ]
    for table in TABLES:
        for entry in data[table]:
            lines += ["", f"[[{table}]]"]
            lines += [f"{k} = {_value(v)}" for k, v in entry.items() if v is not None]
    return "\n".join(lines) + "\n"


def write(spec_path: Path, data: dict[str, Any]) -> Path:
    target = path_for(spec_path)
    target.write_text(dumps(data))
    return target


def read(spec_path: Path) -> dict[str, Any]:
    target = path_for(spec_path)
    if not target.is_file():
        raise SystemExit(f"no lock file: {target} (run `nixsci lab build {spec_path}` first)")
    data = tomllib.loads(target.read_text())
    for table in TABLES:
        data.setdefault(table, [])
    return data


def check(data: dict[str, Any], runs_root: Path, analysis_root: Path) -> list[tuple[str, str, str]]:
    """Compare the store with the lock -> [(kind, what, detail)]; kind is 'missing' or 'changed'."""
    problems: list[tuple[str, str, str]] = []
    for entry in data["run"]:
        directory = runs_root / entry["app"] / entry["run"]
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            problems.append(("missing", entry["run"], "run not in the store"))
            continue
        if sha256_file(manifest) != entry["manifest_sha256"]:
            problems.append(("changed", entry["run"], "manifest differs from the lock"))
        wanted = entry.get("records_sha256")
        for base in (directory, directory / "lab"):
            if (base / "records.jsonl").is_file():
                if wanted and sha256_file(base / "records.jsonl") != wanted:
                    problems.append(("changed", entry["run"], "records differ from the lock"))
                break
        else:
            if wanted:
                problems.append(("missing", entry["run"], "records.jsonl not in the store"))
    for pipeline in data["pipeline"]:
        out = analysis_root / pipeline["name"]
        for rel, digest in pipeline["outputs"].items():
            f = out / rel
            if not f.is_file():
                problems.append(("missing", f"{pipeline['name']}/{rel}", "output not in the store"))
            elif sha256_file(f) != digest:
                problems.append(
                    ("changed", f"{pipeline['name']}/{rel}", "output differs from the lock")
                )
    return problems
