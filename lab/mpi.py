"""mpirun for the hosts of this job. A runner calls `mpi.command(ranks)` and appends its program.

    from nixsci.lab import mpi
    res = mpi.run(mpi.command(8) + ["./a.out"], timeout=120)     # records the MPI setup of this run, once

On one host this is `mpirun -n N`. On several (a job with span > 1) it adds a hostfile of this job's hosts, ranks
placed by slot, and nixsci's launch agent (NIX_DEPLOY_AGENT). Two facts about Open MPI cost a day on a real cluster,
so they are handled here once:

- The launch agent has a different name per generation: `--mca plm_rsh_agent` up to Open MPI 4, `--prtemca
  plm_ssh_agent` from 5. PRRTE 4 ignores the old name without a warning and runs the host's own ssh instead.
- PRRTE forwards the agent's path to the daemons it starts, and a daemon on a host where that path does not exist
  crashes. NIX_DEPLOY_AGENT exists at one path on every host, which is why it is used and a script of the
  runner's own is not."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from . import proc, record

_recorded = False


def hosts() -> list[str]:
    """The hosts of this job, first = this host; empty for a job on one host."""
    path = os.environ.get("NIX_DEPLOY_HOSTFILE")
    if not path:
        return []
    return [h.strip() for h in Path(path).read_text().splitlines() if h.strip()]


def _mpirun_version() -> str:
    out = subprocess.run(["mpirun", "--version"], capture_output=True, text=True).stdout.splitlines()
    return out[0] if out else ""


def _agent_option(agent: str) -> list[str]:
    major = re.search(r"Open MPI\)? *(\d+)", _mpirun_version())
    if major and int(major.group(1)) < 5:
        return ["--mca", "plm_rsh_agent", agent]
    return ["--prtemca", "plm_ssh_agent", agent]


def command(ranks: int, *, per_node: int | None = None, extra: int = 0, bind: str = "none",
            export: tuple[str, ...] | list[str] = ()) -> list[str]:
    """The mpirun words for `ranks` ranks on this job's hosts; the caller appends the program and its arguments.

    per_node  slots per host (default: ranks divided over the hosts, rounded up)
    extra     more slots on the last host, room for a coordinator rank
    bind      --bind-to policy (default none: ranks float over the cores)
    export    environment variables to pass to every rank (-x)"""
    cmd = ["mpirun", "--oversubscribe", "-n", str(ranks), "--bind-to", bind]
    names = hosts()
    if names:
        agent = os.environ.get("NIX_DEPLOY_AGENT")
        if not agent:
            raise RuntimeError("NIX_DEPLOY_AGENT is not set: this nixsci predates the multi-host launch agent")
        each = per_node or -(-ranks // len(names))
        slots = [each] * len(names)
        slots[-1] += extra
        workdir = Path(os.environ.get("NIX_DEPLOY_WORKDIR") or os.getcwd())
        hostfile = workdir / "mpi-hostfile"
        hostfile.write_text("".join(f"{h} slots={s}\n" for h, s in zip(names, slots)))
        cmd += ["--hostfile", str(hostfile), "--map-by", "slot", "--report-bindings", *_agent_option(agent)]
    for name in export:
        cmd += ["-x", name]
    return cmd


def record_config() -> None:
    """Rows of `mpi_config` saying what MPI setup this run had: the account's MCA files, mpirun's version, each
    MCA variable in the environment, and the load average at the start (the machine's, not this run's)."""
    home = Path(os.environ.get("HOME", "/nonexistent"))
    for source, path in (("home-openmpi", home / ".openmpi" / "mca-params.conf"),
                         ("home-prte", home / ".prte" / "mca-params.conf")):
        record("mpi_config", {"source": source, "text": path.read_text() if path.is_file() else ""})
    record("mpi_config", {"source": "version", "text": _mpirun_version()})
    record("mpi_config", {"source": "loadavg", "text": Path("/proc/loadavg").read_text().strip()})
    for name in sorted(os.environ):
        if name.startswith(("OMPI_MCA_", "PRTE_MCA_")):
            record("mpi_config", {"source": "env", "text": f"{name}={os.environ[name]}"})


def run(cmd: list[str], timeout: float) -> proc.Result:
    """Run an mpirun command under a timeout. The first call of a run also records the MPI setup and the
    command line, so a result always says what it ran under."""
    global _recorded
    if not _recorded:
        record_config()
        _recorded = True
    record("mpi_config", {"source": "argv", "text": shlex.join(cmd)})
    return proc.run(cmd, timeout)


__all__ = ["hosts", "command", "run", "record_config"]
