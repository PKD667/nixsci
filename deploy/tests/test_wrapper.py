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


def deployment(program, *, args=(), hardware=None, walltime=None, policy="no", maximum=None, delay=5):
    nix = shutil.which("nix")
    if not nix:
        pytest.skip("Nix is required to build the target wrapper")
    hardware_nix = " ".join(f"{key} = {json.dumps(value)};" for key, value in (hardware or {}).items())
    expr = f'''let f = builtins.getFlake {json.dumps(str(ROOT))};
      p = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
    in f.lib.mkDeployment p {{ name = "wrapper-test";
      program = "${{p.writeShellScript "wrapper-test-program" {json.dumps(program)}}}";
      args = {json.dumps(list(args))};
      hardware = {{ {hardware_nix} }};
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


def test_fractional_walltime_caps_loop_and_kills_setsid_child(tmp_path):
    closure = deployment('sleep 60 & echo $! > "$NIX_DEPLOY_WORKDIR/child"; wait', walltime=0.01, policy="always")
    started = time.monotonic()
    result = launch(closure, tmp_path)
    assert result.returncode == 124
    assert time.monotonic() - started < 4
    assert len((tmp_path / "attempts.jsonl").read_text().splitlines()) == 1
    child = int((tmp_path / "child").read_text())
    for _ in range(100):
        try:
            state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail(f"setsid child {child} survived walltime timeout")


def test_native_rejects_explicit_hardware_mismatch_before_program(tmp_path):
    from nixsci.deploy.native import Native
    from nixsci.deploy.resolver import from_path

    path = deployment('touch "$NIX_DEPLOY_WORKDIR/ran"', hardware={"arch": "not-a-real-architecture"})
    closure = from_path(str(path), "x86_64-linux", {"test": True})
    backend = Native(system="x86_64-linux", run_root=str(tmp_path / "runs"))
    handle = backend.launch(closure, run_id="hardware-mismatch")
    for _ in range(500):
        if not backend.alive(handle):
            break
        time.sleep(0.01)
    assert not backend.alive(handle)
    log = Path(handle["workdir"], "process.log").read_text()
    assert "hardware mismatch: arch" in log
    assert not Path(handle["workdir"], "ran").exists()


def test_native_launch_uses_wrapper_once_and_stop_kills_its_setsid_child(tmp_path):
    from nixsci.deploy.native import Native
    from nixsci.deploy.resolver import from_path

    closure_path = deployment(
        'printf "%s\\n" "$@" > "$NIX_DEPLOY_WORKDIR/args"; sleep 60 & echo $! > "$NIX_DEPLOY_WORKDIR/child"; wait',
        args=("fixed",), policy="always", delay=0.05,
    )
    closure = from_path(str(closure_path), "x86_64-linux", {"test": True})
    assert closure.program == str(closure_path / "bin/run")
    assert closure.metadata["argv"] == []
    assert closure.metadata["metadata"]["originalArgs"] == ["fixed"]
    assert any("util-linux" in path for path in closure.closure)
    backend = Native(system="x86_64-linux", run_root=str(tmp_path / "runs"))
    handle = backend.launch(closure, run_id="integration", argv=("extra",))
    workdir = Path(handle["workdir"])
    child_file = workdir / "child"
    for _ in range(500):
        if child_file.exists() and (workdir / "args").exists():
            break
        time.sleep(0.01)
    assert (workdir / "args").read_text().splitlines() == ["fixed", "extra"]
    child = int(child_file.read_text())
    backend.stop(handle)
    for _ in range(500):
        if not backend.alive(handle):
            break
        time.sleep(0.01)
    assert not backend.alive(handle)
    assert len((workdir / "attempts.jsonl").read_text().splitlines()) == 1
    for _ in range(100):
        try:
            state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail(f"setsid child {child} survived Native.stop")
