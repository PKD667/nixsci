from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import spec as spec_mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nix-lab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run every job of an experiment spec (needs nix-lab[deploy])")
    r.add_argument("spec")
    r.add_argument("--out", default="runs", help="directory receiving <name>/<run>/")
    p = sub.add_parser("plan", help="print the jobs a spec expands to")
    p.add_argument("spec")
    c = sub.add_parser(
        "compact", help="write finished runs' declared datasets as Parquet (needs pyarrow)"
    )
    c.add_argument("runs")
    c.add_argument("--out", default="data")
    a = sub.add_parser("analyze", help="run the spec's R pipelines and record their provenance")
    a.add_argument("spec")
    a.add_argument("--runs", default="runs")
    a.add_argument("--data", default="data")
    a.add_argument("--out", default="analysis")
    a.add_argument("--only")
    args = ap.parse_args(argv)
    if args.cmd == "compact":
        from .compact import compact

        for path in compact(args.runs, args.out):
            print(path)
        return 0
    spec = spec_mod.load(args.spec)
    if args.cmd == "analyze":
        from .analyze import analyze

        codes = analyze(spec, Path(args.runs), Path(args.data), Path(args.out), args.only)
        for name, code in codes.items():
            print(f"{name}: exit {code}")
        return 1 if any(codes.values()) else 0
    if args.cmd == "plan":
        for job in spec.jobs():
            print(f"{job.index:03d} seed={job.seed} params={job.params}")
        return 0
    from .runner import run

    results = run(spec, Path(args.out))
    bad = [m for m in results if m["state"] != "ok"]
    print(f"{len(results) - len(bad)}/{len(results)} ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
