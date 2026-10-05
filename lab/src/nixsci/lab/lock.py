"""`<spec>.lab.lock`: the committed claim of which runs and outputs a project's results rest on.

It holds hashes only. Pipelines are the unit: a published table or figure is the output of a
pipeline, and the pipeline's provenance names exactly the runs it read. The lock therefore lists
each pipeline (fingerprint, output hashes) and each run it consumed (manifest and records hashes,
closure, source). Anyone can `pull` the named runs from any remote and `check` them by hash.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from lab.run import sha256_file

VERSION = 1


def path_for(spec_path: Path) -> Path:
    return spec_path.with_name(spec_path.stem + ".lab.lock")


def collect(spec: Any, runs_root: Path, analysis_root: Path) -> dict[str, Any]:
    """Build the lock data from the store: analysed pipelines and the runs they consumed."""
    pipelines: list[dict[str, Any]] = []
    runs: dict[str, dict[str, Any]] = {}
    for name in sorted(spec.pipelines):
        out = analysis_root / name
        provenance = out / "provenance.json"
        if not provenance.is_file():
            continue
        info = json.loads(provenance.read_text())
        if info.get("exit_code") != 0:
            continue
        outputs = {
            str(f.relative_to(out)): sha256_file(f)
            for f in sorted(out.rglob("*"))
            if f.is_file() and f.name != "provenance.json"
        }
        ids = []
        for item in info["inputs"]:
            directory = runs_root / item["app"] / item["run"]
            manifest = json.loads((directory / "manifest.json").read_text())
            ids.append(item["run"])
            runs[item["run"]] = {
                "app": item["app"],
                "run": item["run"],
                "input_id": manifest.get("input_id"),
                "replicate": manifest.get("replicate"),
                "manifest_sha256": item["manifest_sha256"],
                "records_sha256": manifest.get("records_sha256"),
                "closure": manifest.get("closure"),
                "source": (manifest.get("source") or {}).get("origin"),
                "machine": (manifest.get("machine") or {}).get("hostname"),
            }
        pipelines.append(
            {
                "name": name,
                "fingerprint": info.get("fingerprint"),
                "script_sha256": info.get("script_sha256"),
                "outputs": outputs,
                "runs": sorted(ids),
            }
        )
    return {
        "version": VERSION,
        "app": spec.name,
        "pipeline": pipelines,
        "run": sorted(runs.values(), key=lambda r: r["run"]),
    }


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
        "# Written by `nix-lab lock` (and `build`). Commit it: it is the claim of which runs and",
        "# outputs the results rest on. Hashes only; the data lives in the nix-lab store.",
        f"version = {data['version']}",
        f"app = {json.dumps(data['app'])}",
    ]
    for table in ("pipeline", "run"):
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
        raise SystemExit(f"no lock file: {target} (run `nix-lab build` or `nix-lab lock` first)")
    data = tomllib.loads(target.read_text())
    if data.get("version") != VERSION:
        raise SystemExit(f"{target}: unsupported lock version {data.get('version')!r}")
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
