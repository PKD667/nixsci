"""One target registry and one execution contract for every compute provider."""

import argparse
import json
from pathlib import Path

from .factory import backend, load_targets
from .handles import read, write
from .resolver import resolve


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nix-deploy")
    parser.add_argument("--config", required=True)
    parser.add_argument("--target", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("flake")
    run.add_argument("experiment")
    run.add_argument("run_id")
    run.add_argument("--program", default="run")
    run.add_argument("--arg", action="append", default=[])
    run.add_argument("--env", action="append", default=[])
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--handle", required=True)
    for name in ("status", "stop"):
        commands.add_parser(name).add_argument("handle")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("handle")
    fetch.add_argument("path")
    fetch.add_argument("destination")
    args = parser.parse_args(argv)
    targets = load_targets(args.config)
    transport = backend(args.target, targets)
    if args.command == "run":
        closure = resolve(args.flake, args.experiment, targets[args.target]["system"])
        transport.stage(closure)
        handle = transport.launch(
            closure, run_id=args.run_id, argv=tuple(args.arg),
            env=dict(item.split("=", 1) for item in args.env),
            inputs=dict(item.split("=", 1) for item in args.input), program=args.program,
        )
        write(args.handle, {"target": args.target, **handle})
        print(json.dumps(handle, sort_keys=True))
        return 0
    handle = read(args.handle)
    if handle["target"] != args.target:
        raise ValueError("handle belongs to a different deployment target")
    if args.command == "status":
        print(json.dumps({"id": handle["id"], "alive": transport.alive(handle)}))
    elif args.command == "stop":
        transport.stop(handle)
    else:
        transport.fetch(handle, args.path, Path(args.destination))
    return 0
