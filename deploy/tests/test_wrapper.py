"""Exercise the target-side policy wrapper built by the pinned Nix flake."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def deployment(program, *, walltime=None, policy="no", maximum=None, delay=0):
    nix = shutil.which("nix")
    if not nix:
        pytest.skip("Nix is required to build the target wrapper")
    expr = f'''let f = builtins.getFlake {json.dumps(str(ROOT))};
      p = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
    in f.lib.mkDeployment p {{ name = "wrapper-test";
      program = "${{p.writeShellScript "wrapper-test-program" {json.dumps(program)}}}";
      walltime = {"null" if walltime is None else walltime};
      restart = {{ policy = {json.dumps(policy)}; max = {"null" if maximum is None else maximum}; delay = {delay}; }};
    }}'''
    result = subprocess.run(
        [nix, "build", "--impure", "--no-link", "--print-out-paths", "--expr", expr],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return Path(result.stdout.strip().splitlines()[-1])


def launch(closure, workdir, *args):
    env = {**os.environ, "NIX_DEPLOY_WORKDIR": str(workdir)}
    return subprocess.run([str(closure / "bin/run"), *args], env=env, capture_output=True)


def test_failure_retries_until_success(tmp_path):
    closure = deployment('n=0; [ ! -f "$NIX_DEPLOY_WORKDIR/count" ] || n=$(cat "$NIX_DEPLOY_WORKDIR/count"); n=$((n+1)); echo "$n" > "$NIX_DEPLOY_WORKDIR/count"; [ "$n" -ge 3 ]', policy="on-failure", maximum=4)
    result = launch(closure, tmp_path)
    assert result.returncode == 0
    assert len((tmp_path / "attempts.jsonl").read_text().splitlines()) == 3


def test_maximum_counts_additional_attempts(tmp_path):
    closure = deployment("exit 7", policy="always", maximum=2)
    result = launch(closure, tmp_path)
    assert result.returncode == 7
    assert len((tmp_path / "attempts.jsonl").read_text().splitlines()) == 3


def test_fractional_walltime_caps_the_whole_loop(tmp_path):
    closure = deployment("sleep 5", walltime=0.01, policy="always")
    started = time.monotonic()
    result = launch(closure, tmp_path)
    assert result.returncode == 124
    assert time.monotonic() - started < 4
    assert len((tmp_path / "attempts.jsonl").read_text().splitlines()) == 1


def test_term_stops_child_without_restart(tmp_path):
    closure = deployment('sleep 60 & echo $! > "$NIX_DEPLOY_WORKDIR/child"; wait')
    env = {**os.environ, "NIX_DEPLOY_WORKDIR": str(tmp_path)}
    process = subprocess.Popen([str(closure / "bin/run")], env=env)
    child_file = tmp_path / "child"
    for _ in range(200):
        if child_file.exists():
            break
        time.sleep(0.01)
    assert child_file.exists()
    child = int(child_file.read_text())
    process.terminate()
    process.wait(timeout=5)
    assert len((tmp_path / "attempts.jsonl").read_text().splitlines()) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
