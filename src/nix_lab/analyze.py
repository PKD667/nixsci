"""Run a spec's R pipelines over compacted data, recording what they were fed.

Each `[pipeline.<name>]` names a script and the apps whose runs it reads. The
script is run with Rscript and sees:

    NIX_LAB_DATA   the compacted Parquet root (labr::lab_data reads it)
    NIX_LAB_OUT    this pipeline's output directory, <out>/<name>/
    NIX_LAB_RUNS   the raw runs root (labr::lab_manifests reads it)

and a `provenance.json` is written beside its outputs: the script's hash, every
input run with the hash of its manifest, the R version, and the exit code.
Run it inside the pinned environment (`nix run <nix-lab>#r-env -- ...` or the
flake's `analyze` app) so the R packages are the pinned ones.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .spec import Spec


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def analyze(
    spec: Spec, runs: Path, data: Path, out: Path, only: str | None = None
) -> dict[str, int]:
    rscript = os.environ.get("NIX_LAB_RSCRIPT") or shutil.which("Rscript")
    if not rscript:
        raise SystemExit("Rscript not found: run inside the pinned R environment")
    results = {}
    for name, pipeline in spec.pipelines.items():
        if only and name != only:
            continue
        script = (spec.path.parent / pipeline["script"]).resolve()
        target = out / name
        target.mkdir(parents=True, exist_ok=True)
        inputs = []
        for app in pipeline["inputs"]:
            for manifest in sorted((runs / app).glob("*/manifest.json")):
                inputs.append(
                    {"app": app, "run": manifest.parent.name, "manifest_sha256": _sha(manifest)}
                )
        started = _utc()
        env = {
            **os.environ,
            "NIX_LAB_DATA": str(data),
            "NIX_LAB_OUT": str(target),
            "NIX_LAB_RUNS": str(runs),
        }
        code = subprocess.run([rscript, str(script)], env=env, cwd=target).returncode
        version = subprocess.run([rscript, "--version"], capture_output=True, text=True)
        (target / "provenance.json").write_text(
            json.dumps(
                {
                    "v": 1,
                    "pipeline": name,
                    "script": str(script.relative_to(spec.path.parent)),
                    "script_sha256": _sha(script),
                    "inputs": inputs,
                    "r": (version.stderr or version.stdout).strip(),
                    "started": started,
                    "ended": _utc(),
                    "exit_code": code,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        results[name] = code
    return results
