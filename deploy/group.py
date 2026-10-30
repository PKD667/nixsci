"""Run one program across several leased hosts (an MPI job, a coordinator + workers).

The closure is staged on every host. The program starts once, on the first host,
and learns the rest from its environment:

    NIX_DEPLOY_HOSTFILE   file with one reachable host name per line, first = this host
    NIX_DEPLOY_RSH        command that opens a shell on another host (default: ssh)
    NIX_DEPLOY_ENTER      (set by the backend) prefix that runs a command inside the
                          store view of the host it is executed on, for launch agents
    NIX_DEPLOY_AGENT      an executable `agent HOST CMD...` that runs CMD on HOST inside HOST's store
                          view. It exists at this one path on EVERY host, and needs no environment:
                          its rsh and enter prefixes are written into it. Hand it to a launcher that
                          forwards its launch agent to remote daemons (PRRTE's --prtemca plm_ssh_agent):
                          a daemon whose node has no executable at that path selects no launch
                          component and crashes (observed with PRRTE 4.1.0 and 3.0.12 alike).
"""

from __future__ import annotations

import shlex
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping, Sequence

from .backend import Backend
from .model import Closure

# -F /dev/null: inside a rootless store's user namespace root-owned /etc files look owned by
# nobody and ssh refuses the system config.
DEFAULT_RSH = "ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=accept-new"


def stage_all(backends: Sequence[Backend], hosts: Sequence[str], closure: Closure, rsh: str) -> None:
    """`stage = "fanout"`: the controller stages `backends[0]`; every other host copies from it.

    The first host's link to its peers is `rsh`; the controller's jump and ssh options do not
    apply to that hop. See SSH.receive for what each copy does and what is verified.
    """
    first = backends[0]
    first.stage(closure)
    peers = list(zip(hosts[1:], backends[1:]))
    if not peers:
        return
    with ThreadPoolExecutor(max_workers=len(peers)) as pool:
        list(pool.map(lambda pair: pair[1].receive(closure, source=first, peer=pair[0], rsh=rsh), peers))


def put_agents(backends: Sequence[Backend], closure: Closure, run_id: str, rsh: str) -> str:
    """Write the launch agent on every host, at one path, and return that path.

    The path is the same on every host because it is derived from `run_root`, which the hosts must share
    (a static lease's hosts do). Each copy enters the store view of its own host."""
    roots = {b.run_root for b in backends}
    if len(roots) != 1:
        raise ValueError(f"hosts of one job must share run_root, got {sorted(roots)}")
    for b in backends:
        enter = " ".join(shlex.quote(part) for part in (b.enter(closure) or []))
        text = f'#!/bin/sh\nhost="$1"; shift\nexec {rsh} "$host" {enter} "$@"\n'
        path = b.put_script(f"{b.run_root}/{run_id}", "inputs/agent", text)
    return path


def launch(
    backends: Sequence[Backend],
    hosts: Sequence[str],
    closure: Closure,
    *,
    run_id: str,
    argv: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
    program: str = "run",
    inputs: Mapping[str, Any] | None = None,
    rsh: str | None = None,
) -> dict[str, Any]:
    if not backends or len(backends) != len(hosts):
        raise ValueError("need one backend per host name")
    rsh = rsh or getattr(backends[0], "rsh", None) or DEFAULT_RSH
    if getattr(backends[0], "stage_mode", "direct") == "fanout":
        stage_all(backends, hosts, closure, rsh)
    else:
        with ThreadPoolExecutor(max_workers=len(backends)) as pool:
            list(pool.map(lambda b: b.stage(closure), backends))
    hostfile = f"{backends[0].run_root}/{run_id}/inputs/hostfile"
    agent = put_agents(backends, closure, run_id, rsh)
    values = {"NIX_DEPLOY_RSH": rsh, "NIX_DEPLOY_HOSTFILE": hostfile, "NIX_DEPLOY_AGENT": agent, **(env or {})}
    handle = backends[0].launch(
        closure,
        run_id=run_id,
        argv=argv,
        env=values,
        program=program,
        inputs={**(inputs or {}), "hostfile": ("\n".join(hosts) + "\n").encode()},
    )
    handle["hosts"] = list(hosts)
    return handle


def stop_all(backends: Sequence[Backend], handle: Mapping[str, Any]) -> None:
    backends[0].stop(handle)


__all__ = ["launch", "stop_all", "DEFAULT_RSH"]
