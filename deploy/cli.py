"""One execution contract for every compute provider.

nixsci deploy lease acquire <provider> <name> [--hosts N] [--walltime MIN] [--opt KEY=VALUE]...
nixsci deploy lease ls | show <name> | release <name>
nixsci deploy (--lease NAME | --config FILE --target T) run <flake> <deployment> <run_id> --handle FILE
nixsci deploy (--lease NAME | --config FILE --target T) status|stop|fetch ...
nixsci deploy (--lease NAME | --config FILE --target T) shell <flake> <experiment> [-- CMD ...]
"""

import argparse
import contextlib
import io
import json
import sys
import time
import warnings
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
                    "expires": lease.expires,
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
    parser = argparse.ArgumentParser(prog="nixsci deploy")
    parser.add_argument("--config")
    parser.add_argument("--target")
    parser.add_argument("--lease")
    commands = parser.add_subparsers(dest="command", required=True)
    lease = commands.add_parser("lease")
    lease.add_argument("action", choices=["acquire", "ls", "show", "release"])
    lease.add_argument("provider", nargs="?")
    lease.add_argument("name", nargs="?")
    lease.add_argument("--hosts", type=int, default=1)
    lease.add_argument("--walltime", type=int, default=None, help="minutes the hosts are ours")
    lease.add_argument("--opt", action="append", default=[], help="provider option KEY=VALUE")
    run = commands.add_parser("run")
    run.add_argument("flake")
    run.add_argument("deployment")
    run.add_argument("run_id")
    run.add_argument("--program", default="run")
    run.add_argument("--arg", action="append", default=[])
    run.add_argument("--env", action="append", default=[])
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--handle", required=True)
    shell = commands.add_parser("shell", help="an interactive shell in a run's environment")
    shell.add_argument("flake")
    shell.add_argument("experiment")
    shell.add_argument("--program", default="run")
    shell.add_argument("--env", action="append", default=[])
    shell.add_argument("--input", action="append", default=[])
    shell.add_argument("--run-id", default=None)
    shell.add_argument("--show", action="store_true", help="print the environment, start nothing")
    shell.add_argument("--keep", action="store_true", help="leave the workdir behind")
    for name in ("status", "stop"):
        commands.add_parser(name).add_argument("handle")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("handle")
    fetch.add_argument("path")
    fetch.add_argument("destination")
    raw = list(argv) if argv is not None else sys.argv[1:]
    cut = raw.index("--") if "--" in raw else len(raw)
    args = None
    if "shell" in raw[:cut]:  # `shell ... -- CMD ...`: what follows `--` is the command, not ours
        with contextlib.redirect_stderr(io.StringIO()):  # a real usage error shows on the re-parse
            try:
                args = parser.parse_args(raw[:cut])
            except SystemExit:
                pass
    if args is None or args.command != "shell":
        args, cut = parser.parse_args(raw), len(raw)
    args.cmd = raw[cut + 1 :]

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
        expires = held.expires
    elif args.config and args.target:
        configs = {args.target: factory.load_targets(args.config)[args.target]}
        hosts = [args.target]
        expires = None
    else:
        parser.error("give --lease NAME, or --config FILE with --target T")
    names = list(configs)
    backends = [factory.backend(n, configs) for n in names]
    transport = backends[0]
    if args.command == "shell":
        from .shell import main as shell_main

        return shell_main(args, names, configs, backends, expires)
    if args.command == "run":
        closure = resolve(args.flake, args.deployment, configs[names[0]]["system"])
        walltime = closure.metadata.get("walltime")
        if expires and walltime is not None and walltime * 60 > expires - time.time():
            raise SystemExit("deployment walltime exceeds the lease's remaining time")
        if expires and walltime is None:
            warnings.warn("persistent deployment on a bounded lease: the lease may end first", RuntimeWarning)
        env = dict(item.split("=", 1) for item in args.env)
        if expires:
            if time.time() >= expires:
                raise SystemExit(f"lease {args.lease!r} has expired; acquire the hosts again")
            env["NIX_DEPLOY_DEADLINE"] = str(int(expires))
        kwargs = dict(
            run_id=args.run_id,
            argv=tuple(args.arg),
            program=args.program,
            env=env,
            inputs=dict(item.split("=", 1) for item in args.input),
        )
        if len(backends) > 1:
            handle = group.launch(backends, hosts, closure, **kwargs)
        else:
            transport.stage(closure)
            handle = transport.launch(closure, **kwargs)
        write(args.handle, {"target": names[0], **handle})
        print(json.dumps(handle, sort_keys=True))
        return 0
    handle = read(args.handle)
    if handle["target"] != names[0]:
        raise ValueError("handle belongs to a different deployment target")
    if args.command == "status":
        result = {"id": handle["id"], "alive": transport.alive(handle)}
        attempts = transport.read_file(handle, "attempts.jsonl")
        if attempts:
            result["attempts"] = [json.loads(line) for line in attempts.splitlines() if line]
        print(json.dumps(result))
    elif args.command == "stop":
        transport.stop(handle)
    else:
        transport.fetch(handle, args.path, Path(args.destination))
    return 0
