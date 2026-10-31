"""Run one command under a timeout, in its own process group, so a timeout takes its ranks down with mpirun."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass

KILL_AFTER = 5  # seconds between SIGTERM and SIGKILL


@dataclass(frozen=True)
class Result:
    rc: int | None  # None when the timeout ended the command
    stdout: str
    stderr: str
    seconds: float

    @property
    def timed_out(self) -> bool:
        return self.rc is None


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:  # it ended meanwhile
        pass


def run(cmd: list[str], timeout: float) -> Result:
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
        errors="replace",
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        rc: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        _signal(proc, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=KILL_AFTER)
        except subprocess.TimeoutExpired:
            _signal(proc, signal.SIGKILL)
            out, err = proc.communicate()
        _signal(proc, signal.SIGKILL)  # ranks that outlived mpirun
        rc = None
    return Result(rc, out, err, time.monotonic() - start)
