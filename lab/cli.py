from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from nixsci.lab import home

from . import spec as spec_mod


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nixsci lab")
    ap.add_argument(
        "--store", help="data store (default: $NIX_LAB_STORE or ~/.local/share/nix-lab)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser(
        "build", help="experiment: run what is missing, compact, lock; analysis: run its pipelines, lock"
    )
    b.add_argument("spec")
    b.add_argument("--again", action="store_true", help="add one more replicate per input")
    sub.add_parser("ls", help="what the store holds").add_argument("app", nargs="?")

    p = sub.add_parser("plan", help="print the jobs a spec expands to")
    p.add_argument("spec")
    r = sub.add_parser("run", help="run what is missing (needs nix-lab[deploy])")
    r.add_argument("spec")
    r.add_argument("--out", help="runs directory (default: the store)")
    r.add_argument("--again", action="store_true", help="add one more replicate per input")
    c = sub.add_parser("compact", help="write finished runs' declared datasets as Parquet")
    c.add_argument("runs", nargs="?", help="runs directory (default: the store)")
    c.add_argument("--out", help="Parquet directory (default: the store)")
    c.add_argument("--force", action="store_true", help="rewrite files that look up to date")
    a = sub.add_parser("analyze", help="run an analysis spec's R pipelines over the locked data it [use]s")
    a.add_argument("spec")
    a.add_argument("--runs")
    a.add_argument("--data")
    a.add_argument("--out", help="analysis directory (default: the store)")
    a.add_argument("--only")
    a.add_argument("--force", action="store_true", help="rerun even if nothing changed")

    sub.add_parser("lock", help="write <spec>.lab.lock (an experiment's runs, or an analysis' uses and outputs)").add_argument(
        "spec"
    )
    sub.add_parser("check", help="verify the store against <spec>.lab.lock").add_argument("spec")
    for name, text in (
        ("pull", "fetch what the lock names from REMOTE"),
        ("push", "send it to REMOTE"),
    ):
        s = sub.add_parser(name, help=f"{text} (host:/dir or a path)")
        s.add_argument("remote")
        s.add_argument("spec")
    rp = sub.add_parser("repro", help="print everything needed to run a run's measurement again")
    rp.add_argument("run")
    rp.add_argument("--runs")
    v = sub.add_parser("verify", help="re-run a finished run from its locked source and compare")
    v.add_argument("run")
    v.add_argument("--runs")

    args = ap.parse_args(argv)
    if args.store:
        os.environ["NIX_LAB_STORE"] = args.store
    runs_root = (
        Path(getattr(args, "runs", None) or home.runs_dir()) if args.cmd != "compact" else None
    )

    if args.cmd == "ls":
        from .build import listing

        listing(args.app)
        return 0
    if args.cmd == "compact":
        from .compact import compact

        for path in compact(
            args.runs or home.runs_dir(), args.out or home.data_dir(), force=args.force
        ):
            print(path)
        return 0
    if args.cmd in ("repro", "verify"):
        from . import verify as verify_mod

        run_dir = verify_mod.find_run(args.run, runs_root)
        if args.cmd == "repro":
            print(json.dumps(verify_mod.bundle(run_dir), indent=1, sort_keys=True))
            return 0
        report = verify_mod.verify(run_dir, log=_err)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0 if report["ok"] else 1

    spec = spec_mod.load(args.spec)
    if args.cmd == "plan":
        if spec.kind != "experiment":
            _err(f"{spec.path.name} is an analysis; only an experiment has jobs")
            return 2
        for job in spec.jobs():
            print(f"{job.index:03d} seed={job.seed} params={job.params}")
        return 0
    if args.cmd == "build":
        from .build import build

        return build(spec, again=args.again)
    if args.cmd == "analyze":
        from .analysis import analyze

        results = analyze(
            spec,
            Path(args.runs or home.runs_dir()),
            Path(args.data or home.data_dir()),
            Path(args.out or home.analysis_dir() / spec.name),
            args.only,
            args.force,
        )
        for name, (code, skipped) in results.items():
            print(f"{name}: " + ("up to date" if skipped else f"exit {code}"))
        return 1 if any(code for code, _ in results.values()) else 0
    if args.cmd == "lock":
        from . import lock

        written = lock.write(
            spec.path, lock.collect(spec, home.runs_dir(), home.analysis_dir() / spec.name)
        )
        print(written)
        return 0
    if args.cmd in ("check", "pull", "push"):
        from . import lock, sync

        data = lock.read(spec.path)
        if args.cmd == "push":
            failed = sync.push(args.remote, data, home.store_root())
            return 1 if failed else 0
        if args.cmd == "pull":
            problems = sync.pull(args.remote, data, home.store_root())
        else:
            problems = lock.check(data, home.runs_dir(), home.analysis_dir() / data["app"])
        for kind, what, detail in problems:
            print(f"{kind}: {what}: {detail}")
        print("store matches the lock" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    from .runner import run

    runs = run(spec, Path(args.out or home.runs_dir()), again=args.again)
    bad = [m for m in runs if m["state"] != "ok"]
    print(
        f"{len(runs) - len(bad)}/{len(runs)} new run(s) ok"
        if runs
        else "nothing to run: up to date"
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
