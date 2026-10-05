"""`nixsci lab`: the executor and the tools around it.

Nix owns the spec, the closures and every pure step (compaction, analyses). This command runs the
one impure step, `exec` (started by `nix run .#<experiment>`), and handles the files it leaves.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from nixsci.lab import home

from . import inputs, publish, spec as spec_mod


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _listing(project: Path | None, app: str | None) -> None:
    root = home.runs_dir(project)
    if not root.is_dir():
        print(f"(no runs in {root})")
        return
    for app_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if app and app_dir.name != app:
            continue
        found: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for manifest in app_dir.glob("*/manifest.json"):
            m = json.loads(manifest.read_text())
            found[m.get("input_id") or m["run"]][m.get("state", "?")] += 1
        print(f"{app_dir.name}: {len(found)} input(s), {sum(sum(s.values()) for s in found.values())} run(s)")
        if app:
            for ident, states in sorted(found.items()):
                print(f"  {ident[:12]}  " + ", ".join(f"{n} {s}" for s, n in sorted(states.items())))


def _copy(direction: str, remote: str, project: Path) -> int:
    paths = [e["path"] for entries in publish.read_lock(project).values() for e in entries]
    if not paths:
        _err(f"{project / 'lab.lock.json'} lists no runs")
        return 1
    cmd = ["nix", "--extra-experimental-features", "nix-command", "copy", "--no-check-sigs", f"--{direction}", remote, *paths]
    return subprocess.run(cmd).returncode


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
    ap.add_argument("--project", help="project directory (default: the nearest directory with a .git)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("exec", help="run an experiment's missing jobs (started by `nix run .#<experiment>`)")
    e.add_argument("--spec", required=True, help="the spec JSON Nix wrote")
    e.add_argument("--closure", action="append", default=[], metavar="SYSTEM=PATH", help="the built closure for a system")
    e.add_argument("--source", default="{}", help="the flake's source identity, as JSON")
    e.add_argument("--again", action="store_true", help="add one more replicate per input")
    p = sub.add_parser("plan", help="print the jobs a spec expands to")
    p.add_argument("--spec", required=True)
    sub.add_parser("ls", help="what the runs directory holds").add_argument("app", nargs="?")
    sub.add_parser("add", help="add a finished run directory to the Nix store and lab.lock.json").add_argument("run")
    for name, text in (("push", "copy the locked runs to REMOTE"), ("pull", "copy the locked runs from REMOTE")):
        sub.add_parser(name, help=f"{text} (a Nix store URL, e.g. ssh-ng://host)").add_argument("remote")
    rp = sub.add_parser("repro", help="print everything needed to run a run's measurement again")
    rp.add_argument("run")
    v = sub.add_parser("verify", help="run one more replicate of a finished run and compare")
    v.add_argument("run")

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
    project = home.project_root(args.project)

    if args.cmd == "data":
        return _data(args)
    if args.cmd == "ls":
        _listing(project, args.app)
        return 0
    if args.cmd == "add":
        entry = publish.publish(Path(args.run), project)
        print(f"{entry['name']}  {entry['path']}")
        return 0
    if args.cmd in ("push", "pull"):
        return _copy("to" if args.cmd == "push" else "from", args.remote, project)
    if args.cmd in ("repro", "verify"):
        from . import verify as verify_mod

        run_dir = verify_mod.find_run(args.run, home.runs_dir(project))
        if args.cmd == "repro":
            print(json.dumps(verify_mod.bundle(run_dir), indent=1, sort_keys=True))
            return 0
        report = verify_mod.verify(run_dir, log=_err)
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0 if report["ok"] else 1

    spec = spec_mod.read(args.spec)
    if args.cmd == "plan":
        for job in spec.jobs():
            print(f"{job.index:03d} seed={job.seed} params={job.params}")
        return 0

    from nixsci.deploy import resolver

    source = json.loads(args.source)
    closures = {}
    for item in args.closure:
        system, _, path = item.partition("=")
        closures[system] = resolver.from_path(path, system, source)
    if not closures:
        _err("give at least one --closure SYSTEM=PATH")
        return 2
    from .runner import run

    runs = run(spec, closures, home.runs_dir(project), project=project, again=args.again)
    bad = [m for m in runs if m["state"] != "ok"]
    print(f"{len(runs) - len(bad)}/{len(runs)} new run(s) ok" if runs else "nothing to run: up to date")
    if runs:
        print(f"commit {project / 'lab.lock.json'} to keep these runs")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
