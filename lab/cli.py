from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from nixsci.lab import home

from . import inputs, spec as spec_mod


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _data(args) -> int:
    store = inputs.Store(args.inputs or home.inputs_root())
    try:
        if args.action == "import":
            if args.kind == "dataset":
                units = dict(item.split("=", 1) for item in args.unit)
                sha, new = store.import_dataset(args.name, args.file, units=units, source=args.source, parent=args.parent)
            else:
                io = json.loads(args.io) if args.io else None
                sha, new = store.import_model(args.name, args.file, io=io, source=args.source, parent=args.parent)
            body = store.manifest(sha)
            print(f"{args.name}@{body['number']}  {sha[:12]}  {'new' if new else 'unchanged'}")
            for gap in body["gaps"]:
                print(f"  gap: {gap}")
        elif args.action == "ls":
            for name in store.names():
                tips = store.tips(name)
                body = next(iter(tips.values()))
                number = f"@{body['number']}" if len(tips) == 1 else f"fork of {len(tips)}"
                rows = body["details"].get("rows")
                print(f"{name}  {body['kind']}  {number}" + (f"  {rows} rows" if rows is not None else ""))
        elif args.action == "diff":
            print(json.dumps(store.diff(store.resolve(args.a), store.resolve(args.b)), indent=1, sort_keys=True))
        else:
            sha = store.resolve(args.ref)
            if args.action == "show":
                print(json.dumps(store.manifest(sha), indent=1, sort_keys=True))
            elif args.action == "path":
                for blob in store.manifest(sha)["blobs"]:
                    print(store.blob_path(blob["sha256"]))
            else:
                problems = store.verify(sha)
                for name, what in problems:
                    print(f"{what}: {name}")
                print("files match their hashes" if not problems else f"{len(problems)} problem(s)")
                return 1 if problems else 0
    except (inputs.Missing, inputs.Ambiguous, ValueError) as error:
        _err(f"nixsci lab data: {error}")
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nixsci lab")
    ap.add_argument(
        "--store", help="outputs store (default: <project>/.nixsci, or $NIXSCI_STORE)"
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
    r = sub.add_parser("run", help="run what is missing")
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

    d = sub.add_parser("data", help="datasets and models in the input store")
    d.add_argument("--inputs", help="input store (default: $NIXSCI_INPUTS or ~/.nixsci)")
    actions = d.add_subparsers(dest="action", required=True)
    i = actions.add_parser("import", help="add a dataset (a Parquet file) or a model (any file) as a revision")
    i.add_argument("kind", choices=inputs.KINDS)
    i.add_argument("name")
    i.add_argument("file")
    i.add_argument("--unit", action="append", default=[], metavar="COLUMN=UNIT", help="a dataset column's unit")
    i.add_argument("--io", help="a model's inputs and outputs, as JSON")
    i.add_argument("--source", action="append", default=[], help="where the bytes can be fetched again")
    i.add_argument("--parent", help="hash of the revision to extend (default: the tip)")
    actions.add_parser("ls", help="every name with its tip")
    for action, text in (
        ("show", "print a revision's manifest"),
        ("verify", "re-hash a revision's files"),
        ("path", "print where a revision's files are"),
    ):
        actions.add_parser(action, help=text).add_argument("ref", help="name, name@3 or name#<hash>")
    df = actions.add_parser("diff", help="columns, rows and bytes that changed between two revisions")
    df.add_argument("a")
    df.add_argument("b")

    args = ap.parse_args(argv)
    if args.store:
        os.environ["NIXSCI_STORE"] = args.store
    near = getattr(args, "spec", None)
    runs_root = (
        Path(getattr(args, "runs", None) or home.runs_dir(near)) if args.cmd != "compact" else None
    )

    if args.cmd == "data":
        return _data(args)
    if args.cmd == "ls":
        from .build import listing

        listing(args.app)
        return 0
    if args.cmd == "compact":
        from .compact import compact

        for path in compact(
            args.runs or home.runs_dir(near), args.out or home.data_dir(near), force=args.force
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
            Path(args.runs or home.runs_dir(near)),
            Path(args.data or home.data_dir(near)),
            Path(args.out or home.analysis_dir(near) / spec.name),
            args.only,
            args.force,
        )
        for name, (code, skipped) in results.items():
            print(f"{name}: " + ("up to date" if skipped else f"exit {code}"))
        return 1 if any(code for code, _ in results.values()) else 0
    if args.cmd == "lock":
        from . import lock

        written = lock.write(
            spec.path, lock.collect(spec, home.runs_dir(near), home.analysis_dir(near) / spec.name)
        )
        print(written)
        return 0
    if args.cmd in ("check", "pull", "push"):
        from . import lock, sync

        data = lock.read(spec.path)
        if args.cmd == "push":
            failed = sync.push(args.remote, data, home.store_root(near))
            return 1 if failed else 0
        if args.cmd == "pull":
            problems = sync.pull(args.remote, data, home.store_root(near))
        else:
            problems = lock.check(data, home.runs_dir(near), home.analysis_dir(near) / data["app"])
        for kind, what, detail in problems:
            print(f"{kind}: {what}: {detail}")
        print("store matches the lock" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    from .runner import run

    runs = run(spec, Path(args.out or home.runs_dir(near)), again=args.again)
    bad = [m for m in runs if m["state"] != "ok"]
    print(
        f"{len(runs) - len(bad)}/{len(runs)} new run(s) ok"
        if runs
        else "nothing to run: up to date"
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
