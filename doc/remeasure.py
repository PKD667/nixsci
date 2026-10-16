"""Run every declared job again, then build a document against only the fresh lock."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _closure_args(closures: dict[str, str]) -> list[str]:
    return [part for system, path in closures.items() for part in ("--closure", f"{system}={path}")]


def _run(command: list[str], **kwargs) -> str:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def _build_plan(ref: str, doc: str) -> tuple[Path, str]:
    meta = json.loads(_run(["nix", "flake", "metadata", "--json", ref]))
    # Freeze the input before any impure jobs run; never read a changing checkout afterward.
    frozen = meta.get("path") or meta["resolvedUrl"]
    system = _run(["nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"])
    out = _run(["nix", "build", "--no-link", "--print-out-paths", f"{frozen}#packages.{system}.{doc}.measurementPlan"])
    return Path(out), frozen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nixsci doc remeasure")
    ap.add_argument("flake_doc", help="FLAKE#DOC, where DOC is an exported package PDF")
    ap.add_argument("--level", type=float, default=0.95)
    ap.add_argument("--output", type=Path, default=Path("remeasure"))
    args = ap.parse_args(argv)
    if "#" not in args.flake_doc:
        ap.error("expected FLAKE#DOC")
    ref, doc = args.flake_doc.rsplit("#", 1)
    if not ref or not doc or "." in doc:
        ap.error("DOC must be a package name (without an attribute suffix)")

    if not math.isfinite(args.level) or not 0.5 < args.level < 1:
        ap.error("--level must be finite and strictly between 0.5 and 1")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        ap.error(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="nixsci-remeasure-"))
    try:
        plan_path, frozen = _build_plan(ref, doc)
        plan = json.loads(plan_path.read_text())
        if not plan:
            raise RuntimeError("document has no traced lab analyses to remeasure")
        locks: dict[str, dict[str, list]] = {}
        by_run: dict[tuple, dict[str, list]] = {}
        specs = output / "specs"
        specs.mkdir(exist_ok=True)
        for item in plan:
            spec_path = Path(item["specFile"])
            spec = json.loads(spec_path.read_text())
            closures = item["closures"]
            source = json.loads(item["source"])
            key = (str(spec_path), json.dumps(closures, sort_keys=True), json.dumps(source, sort_keys=True))
            if key not in by_run:
                project = work / f"run-{len(by_run)}"
                project.mkdir()
                store = work / f"store-{len(by_run)}"
                command = [sys.executable, "-m", "nixsci.lab.cli", "--project", str(project), "exec", "--spec", str(spec_path), "--source", json.dumps(source)]
                command.extend(_closure_args(closures))
                env = os.environ.copy()
                env["NIXSCI_STORE"] = str(store)
                _run(command, env=env, cwd=project)
                fresh = json.loads((project / "lab.lock").read_text())["runs"]
                by_run[key] = fresh
            locks.setdefault(item["analysis"], {}).update(by_run[key])
        lock_map = {analysis: runs for analysis, runs in locks.items()}
        lock_file = output / "locks.json"
        lock_file.write_text(json.dumps(lock_map, indent=2, sort_keys=True) + "\n")
        shutil.copy2(plan_path, output / "measurement-plan.json")
        prefix = ("let f = builtins.getFlake " + json.dumps(frozen) + "; p = f.packages.${builtins.currentSystem}." + json.dumps(doc) + "; "
                  "locks = builtins.fromJSON (builtins.readFile " + json.dumps(str(lock_file)) + "); ")
        info = {}
        for item in plan:
            info.setdefault(item["analysis"], item)
        reports = output / "reports"
        reports.mkdir(exist_ok=True)
        summaries = {"contradicted": 0, "inconclusive": 0, "unsupported": 0}
        for analysis, item in info.items():
            view_expr = prefix + "in (p.remeasureViews { inherit locks; level = " + str(args.level) + "; })." + json.dumps(analysis)
            view = _run(["nix", "build", "--impure", "--no-link", "--print-out-paths", "--expr", view_expr])
            report = reports / (Path(analysis).name + ".json")
            command = [sys.executable, "-m", "nixsci.stats.claims", item["claimFile"], "--view", item["view"], "--reproduction", view, "--level", str(args.level), "--json", str(report)]
            judged = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if judged.returncode not in (0, 1, 3):
                raise RuntimeError(f"claim judging failed: {judged.stdout}{judged.stderr}")
            body = json.loads(report.read_text())
            for claim in body["claims"].values():
                original = claim["original"]["verdict"]
                reproduced = claim.get("reproduced", original)
                if reproduced in ("contradicted", "no"):
                    summaries["contradicted"] += 1
                if reproduced == "inconclusive":
                    summaries["inconclusive"] += 1
                if reproduced == "unsupported":
                    summaries["unsupported"] += 1
        note = (f"{summaries['contradicted']} contradicted; {summaries['inconclusive']} inconclusive; "
                f"{summaries['unsupported']} unsupported original claims; per-claim interval confidence {args.level:.2f}; no joint guarantee")
        (reports / "summary.json").write_text(json.dumps({"level": args.level, **summaries,
            "formalVerification": "not-performed", "confidenceScope": "per-claim; no joint guarantee"}, indent=2, sort_keys=True) + "\n")
        if any(summaries.values()):
            print("nixsci doc remeasure: WARNING: " + note, file=sys.stderr)
        expression = prefix + "in p.withMeasurements { inherit locks; level = " + str(args.level) + "; remeasurement = " + json.dumps(note) + "; }"
        pdf = _run(["nix", "build", "--impure", "--no-link", "--print-out-paths", "--expr", expression])
        source_pdf = Path(pdf) / f"{doc}.pdf"
        if not source_pdf.is_file():
            candidates = list(Path(pdf).glob("*.pdf"))
            if len(candidates) != 1:
                raise RuntimeError(f"measured document output has no unique PDF: {pdf}")
            source_pdf = candidates[0]
        shutil.copy2(source_pdf, output / source_pdf.name)
        print(f"remeasured PDF: {output / source_pdf.name}")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as error:
        diagnostics = output / "diagnostics"
        if work.exists():
            shutil.copytree(work, diagnostics, dirs_exist_ok=True)
        print(f"nixsci doc remeasure: {error}", file=sys.stderr)
        print(f"diagnostics retained at {output}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
