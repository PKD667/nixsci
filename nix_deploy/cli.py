"""One execution contract for every compute provider.

nix-deploy lease acquire <provider> <name> [--hosts N] [--walltime MIN] [--opt KEY=VALUE]...
nix-deploy lease ls | show <name> | release <name>
nix-deploy (--lease NAME | --config FILE --target T) run <flake> <experiment> <run_id> --handle FILE
nix-deploy (--lease NAME | --config FILE --target T) status|stop|fetch ...
"""

import argparse
import json
from pathlib import Path

from . import factory, group, leases
from .handles import read, write
from .resolver import resolve


def _lease_command(args) -> int:
    if args.action == "acquire":
        resources = {"hosts": args.hosts, "walltime": args.walltime}
        opts = dict(item.split("=", 1) for item in args.opt)
        lease = leases.acquire(args.name, args.provider, resources, opts)
        print(
            json.dumps(
                {
                    "name": args.name,
                    "hosts": lease.hosts or [c.get("host", "local") for c in lease.targets],
                }
            )
        )
    elif args.action == "ls":
        print("\n".join(leases.names()))
    elif args.action == "show":
        print(json.dumps(leases.load(args.name).targets, indent=1))
    else:
        leases.release(args.name)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nix-deploy")
    parser.add_argument("--config")
    parser.add_argument("--target")
    parser.add_argument("--lease")
    commands = parser.add_subparsers(dest="command", required=True)
    lease = commands.add_parser("lease")
    lease.add_argument("action", choices=["acquire", "ls", "show", "release"])
    lease.add_argument("provider", nargs="?")
    lease.add_argument("name", nargs="?")
    lease.add_argument("--hosts", type=int, default=1)
    lease.add_argument("--walltime", type=int, default=60, help="minutes")
    lease.add_argument("--opt", action="append", default=[], help="provider option KEY=VALUE")
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

    if args.command == "lease":
        if args.action == "acquire" and not (args.provider and args.name):
            parser.error("lease acquire needs <provider> <name>")
        if args.action in ("show", "release"):
            args.name = args.name or args.provider
        return _lease_command(args)

    if args.lease:
        held = leases.load(args.lease)
        configs = {f"t{i}": c for i, c in enumerate(held.targets)}
        hosts = held.hosts or list(configs)
    elif args.config and args.target:
        configs = {args.target: factory.load_targets(args.config)[args.target]}
        hosts = [args.target]
    else:
        parser.error("give --lease NAME, or --config FILE with --target T")
    names = list(configs)
    backends = [factory.backend(n, configs) for n in names]
    transport = backends[0]
    if args.command == "run":
        closure = resolve(args.flake, args.experiment, configs[names[0]]["system"])
        kwargs = dict(
            run_id=args.run_id,
            argv=tuple(args.arg),
            program=args.program,
            env=dict(item.split("=", 1) for item in args.env),
        )
        if len(backends) > 1:
            handle = group.launch(backends, hosts, closure, **kwargs)
        else:
            transport.stage(closure)
            handle = transport.launch(
                closure, inputs=dict(item.split("=", 1) for item in args.input), **kwargs
            )
        write(args.handle, {"target": names[0], **handle})
        print(json.dumps(handle, sort_keys=True))
        return 0
    handle = read(args.handle)
    if handle["target"] != names[0]:
        raise ValueError("handle belongs to a different deployment target")
    if args.command == "status":
        print(json.dumps({"id": handle["id"], "alive": transport.alive(handle)}))
    elif args.command == "stop":
        transport.stop(handle)
    else:
        transport.fetch(handle, args.path, Path(args.destination))
    return 0
