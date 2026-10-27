"""Stage a closure on a job's hosts by copying it from the first host, not from the controller.

Default staging (`stage = "direct"`) copies the closure from the controller to every host, so a
job on k hosts sends it k times over the controller's uplink. With `stage = "fanout"` on the
first target:

1. the controller stages the first host, as before (one copy over the controller's link);
2. each other host receives the Nix bootstrap and the missing store paths from the first host,
   through a pipe that runs on the first host:
       nix-store --export <missing> | <rsh> <peer> 'nix-store --import'
   `rsh` is the job's NIX_DEPLOY_RSH, the link the program already uses to reach its peers.
3. every host verifies its own closure against the NAR hashes (`_verify`), as before.

Why this works with rootless stores: each export and import runs with that host's own bootstrap
against that host's own store, under the user's own privileges. No root, no shared filesystem,
no store path is copied by name. The stream is Nix's export format; a corrupt or incomplete
copy fails the NAR-hash check on the receiving host, so the first host is trusted for transport
only, never for identity.
"""

from __future__ import annotations

import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from . import nix
from .model import Closure


def stage_all(backends: Sequence[Any], hosts: Sequence[str], closure: Closure, rsh: str) -> None:
    """The controller stages `backends[0]`; every other host copies from it, in parallel."""
    first = backends[0]
    first.stage(closure)
    peers = list(zip(hosts[1:], backends[1:]))
    if not peers:
        return
    with ThreadPoolExecutor(max_workers=len(peers)) as pool:
        list(pool.map(lambda pair: pair[1].receive(closure, source=first, peer=pair[0], rsh=rsh), peers))


def pipe(source: Any, setup: str, producer: str, peer: str, rsh: str, consumer: str, cleanup: str) -> None:
    """On the source host: `setup`, then `producer | rsh peer consumer`, then `cleanup`.

    The exit status of the pipe is the consumer's; cleanup runs either way.
    """
    source._remote(
        f"{setup}; {producer} | {rsh} {shlex.quote(peer)} {shlex.quote(consumer)}; "
        f"rc=$?; {cleanup}; exit $rc"
    )


def frontend(directory: str, store: str, *args: str) -> str:
    """A `nix-store` frontend (the bootstrap under that name) with the store's flags, as shell text."""
    argv = nix.command(f"{directory}/nix-store", store, *args)
    return f'"{directory}/nix-store"' + "".join(f" {shlex.quote(a)}" for a in argv[1:])


def receive(dest: Any, closure: Closure, *, source: Any, peer: str, rsh: str) -> dict[str, Any]:
    """Make `closure` present and verified on `dest`, copying from `source` (the first host)."""
    dest._admit(closure)
    dest._wait_ready()
    if not dest._bootstrap_shipped:
        parent = str(Path(dest.remote_bootstrap).parent)
        pipe(
            source,
            "true",
            f"cat {shlex.quote(source.remote_bootstrap)}",
            peer,
            rsh,
            f"umask 077; mkdir -p {shlex.quote(parent)}; cat > {shlex.quote(dest.remote_bootstrap)}; "
            f"chmod 700 {shlex.quote(dest.remote_bootstrap)}",
            "true",
        )
        dest._check_bootstrap()
        dest._bootstrap_shipped = True
    cached = dest._remote_hashes()
    for path, expected in closure.closure.items():
        if path in cached and cached[path] != expected:
            raise RuntimeError(f"preexisting target path NAR identity differs: {path}")
    missing = [path for path in closure.closure if path not in cached]
    if missing:
        src_stage, dst_stage = f"{source.run_root}/.stage", f"{dest.run_root}/.stage"
        pipe(
            source,
            f"umask 077; mkdir -p {shlex.quote(src_stage)}; "
            f"d=$(mktemp -d {shlex.quote(src_stage)}/fanout-XXXXXXXX); "
            f"ln -s {shlex.quote(source.remote_bootstrap)} \"$d/nix-store\"",
            frontend("$d", source.store, "--export", *missing),
            peer,
            rsh,
            f"umask 077; mkdir -p {shlex.quote(dst_stage)}; "
            f"d=$(mktemp -d {shlex.quote(dst_stage)}/fanout-XXXXXXXX); "
            f"ln -s {shlex.quote(dest.remote_bootstrap)} \"$d/nix-store\"; "
            f"{frontend('$d', dest.store, '--import')}; rm -rf -- \"$d\"",
            'rm -rf -- "$d"',
        )
    dest._verify(closure)
    return {"path": closure.path, "closure": dict(closure.closure), "system": closure.system}
