"""`nixsci deploy shell`: an interactive shell in the environment a bare `run` gives its program.

The environment comes from `Backend.job_env`, the method `launch` uses, so the two cannot drift.
The program is not executed. The one variable a job does not get is `NIX_DEPLOY_PROGRAM`, the
path of the program, so that `"$NIX_DEPLOY_PROGRAM" "$@"` starts it by hand.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Any, Mapping

from .backend import Backend
from .model import Closure
from .resolver import resolve

#: Found by `nix shell` on the closure's PATH first, then the host's.
SHELL = ("bash", "--noprofile", "--norc", "-i")


def _quote(parts) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def command(
    backend: Backend, closure: Closure, workdir: str, values: Mapping[str, str], cmd
) -> tuple[list[str], str | None]:
    """`(argv, cwd)` that runs `cmd` under `env -i` with exactly `values`, inside the store view."""
    enter = backend.enter(closure)
    if not enter:
        raise RuntimeError(f"{backend.backend_name} target cannot enter a closure")
    pairs = [f"{key}={value}" for key, value in sorted(values.items())]
    ssh = getattr(backend, "_ssh", None)  # the ssh backend's own option-bearing command
    if ssh is None:
        return ["env", "-i", *pairs, *enter, *cmd], workdir
    # As the ssh backend's launch script: a remote HOME is only known remotely.
    home = [] if "HOME" in values else ['HOME="$HOME"']
    script = f"cd {shlex.quote(workdir)} && exec env -i {' '.join(home)} {_quote(pairs)} "
    return [*ssh, "-t", backend.host, script + _quote([*enter, *cmd])], None


def _interact(argv: list[str], cwd: str | None) -> int:
    # ^C belongs to the shell in front of us, not to this process.
    old = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        status = subprocess.run(argv, cwd=cwd).returncode
    finally:
        signal.signal(signal.SIGINT, old)
    return 128 - status if status < 0 else status


def shell(
    backend: Backend,
    closure: Closure,
    *,
    target: str = "target",
    run_id: str | None = None,
    program: str = "run",
    env: Mapping[str, str] | None = None,
    inputs: Mapping[str, Any] | None = None,
    cmd=(),
    show: bool = False,
    keep: bool = False,
    out=None,
    err=None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    run_id = run_id or f"shell-{int(time.time())}-{os.getpid()}"
    backend.stage(closure)
    workdir = backend._workdir(run_id)
    if backend.exists({"workdir": workdir}, "."):
        raise FileExistsError(f"{workdir} exists already; pick another --run-id")
    try:
        executable, args, values, _ = backend.job_env(closure, run_id, (), env, inputs, program)
    except BaseException:
        try:  # job_env may have made the workdir before failing; it did not exist before
            backend.remove({"workdir": workdir})
        except Exception:
            pass
        raise
    values["NIX_DEPLOY_PROGRAM"] = executable
    try:
        print(
            f"nixsci deploy shell: target={target} closure={closure.path} workdir={workdir}",
            file=err,
            flush=True,
        )
        if show:
            json.dump(
                {
                    "target": target,
                    "closure": closure.path,
                    "workdir": workdir,
                    "program": executable,
                    "argv": list(args),
                    "enter": backend.enter(closure),
                    "env": [f"{k}={v}" for k, v in sorted(values.items())],
                    "env_from_target": [] if "HOME" in values else ["HOME"],
                },
                out,
                indent=1,
                sort_keys=True,
            )
            out.write("\n")
            return 0
        argv, cwd = command(backend, closure, workdir, values, cmd or SHELL)
        return _interact(argv, cwd)
    finally:
        if keep:
            print(f"nixsci deploy shell: kept workdir {workdir}", file=err, flush=True)
        else:
            try:
                backend.remove({"workdir": workdir})
            except Exception as error:  # the shell's status matters more than the cleanup
                print(f"nixsci deploy shell: could not remove {workdir}: {error}", file=err)


def main(args, names, configs, backends, expires) -> int:
    """The CLI glue: resolve, then `shell`. `args.cmd` is what followed `--`."""
    if len(backends) > 1:
        raise SystemExit(
            "shell enters one host; a multi-host run also gets NIX_DEPLOY_HOSTFILE and the "
            "hostfile input, so use a lease or target with a single host"
        )
    closure = resolve(args.flake, args.experiment, configs[names[0]]["system"])
    env = dict(item.split("=", 1) for item in args.env)
    if expires:  # as `run`
        if time.time() >= expires:
            raise SystemExit(f"lease {args.lease!r} has expired; acquire the hosts again")
        env["NIX_DEPLOY_DEADLINE"] = str(int(expires))
    return shell(
        backends[0],
        closure,
        target=names[0],
        run_id=args.run_id,
        program=args.program,
        env=env,
        inputs=dict(item.split("=", 1) for item in args.input),
        cmd=args.cmd,
        show=args.show,
        keep=args.keep,
    )
