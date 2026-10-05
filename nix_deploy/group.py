"""Run one program across several leased hosts (an MPI job, a coordinator + workers).

The closure is staged on every host. The program starts once, on the first host,
and learns the rest from its environment:

    NIX_DEPLOY_HOSTFILE   file with one reachable host name per line, first = this host
    NIX_DEPLOY_RSH        command that opens a shell on another host (default: ssh)
    NIX_DEPLOY_ENTER      (set by the backend) prefix that runs a command inside the
                          store view of the host it is executed on, for launch agents
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping, Sequence

from .backend import Backend
from .model import Closure

# -F /dev/null: inside a rootless store's user namespace root-owned /etc files look owned by
# nobody and ssh refuses the system config.
DEFAULT_RSH = "ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=accept-new"


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
    rsh: str = DEFAULT_RSH,
) -> dict[str, Any]:
    if not backends or len(backends) != len(hosts):
        raise ValueError("need one backend per host name")
    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        list(pool.map(lambda b: b.stage(closure), backends))
    hostfile = f"{backends[0].run_root}/{run_id}/inputs/hostfile"
    values = {"NIX_DEPLOY_RSH": rsh, "NIX_DEPLOY_HOSTFILE": hostfile, **(env or {})}
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
