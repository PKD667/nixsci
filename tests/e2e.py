"""End to end on any provider: lease hosts, run the `hello` experiment on each, show what it printed.

    python tests/e2e.py <provider> [hosts] [--flake REF] [--opt KEY=VALUE]...

`provider` is a name from ~/.config/nix-deploy/providers.toml, or a builtin (local, static, oar).
The lease is released even when something fails.
"""

import argparse
import sys
import time
from pathlib import Path

from nix_deploy import factory, group, providers, resolver


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider")
    ap.add_argument("hosts", nargs="?", type=int, default=1)
    ap.add_argument("--flake", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--walltime", type=int, default=20, help="minutes")
    ap.add_argument(
        "--opt", action="append", default=[], help="provider option, e.g. queue=besteffort"
    )
    args = ap.parse_args()

    lease = providers.acquire(
        args.provider,
        {"hosts": args.hosts, "walltime": args.walltime},
        dict(item.split("=", 1) for item in args.opt),
    )
    try:
        configs = {f"t{i}": c for i, c in enumerate(lease.targets)}
        backends = [factory.backend(name, configs) for name in configs]
        system = lease.targets[0]["system"]
        closure = resolver.resolve(args.flake, "hello", system)
        print(
            f"closure {closure.path} on {lease.hosts or [c.get('host', 'local') for c in lease.targets]}"
        )
        handle = group.launch(
            backends, lease.hosts or list(configs), closure, run_id=f"e2e-{int(time.time())}"
        )
        while backends[0].alive(handle):
            time.sleep(1)
        print(backends[0].read_file(handle, "process.log"))
        backends[0].remove(handle)
        return 0
    finally:
        lease.release()


if __name__ == "__main__":
    sys.exit(main())
