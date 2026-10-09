"""Exercise the existing deployment lifecycle on one private-store target."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from nixsci.deploy.model import Closure
from nixsci.deploy.native import Native
from nixsci.deploy.resolver import resolve
from nixsci.deploy.ssh import SSH


def local_host_snapshot():
    store = Path("/nix/store")
    result = {
        "uid": os.getuid(),
        "mount_namespace": os.readlink("/proc/self/ns/mnt"),
        "mountinfo_sha256": hashlib.sha256(Path("/proc/self/mountinfo").read_bytes()).hexdigest(),
        "host_store_exists": store.exists(),
    }
    if result["host_store_exists"]:
        info = store.stat()
        result["host_store_stat"] = f"{info.st_dev}:{info.st_ino}:{info.st_uid}:{info.st_mode & 0o777:o}"
    return result


def remote_host_snapshot(backend):
    result = backend._remote(
        "printf 'uid=%s\\n' \"$(id -u)\"; "
        "printf 'mount_namespace=%s\\n' \"$(readlink /proc/self/ns/mnt)\"; "
        "printf 'mountinfo_sha256=%s\\n' \"$(sha256sum /proc/self/mountinfo | cut -d' ' -f1)\"; "
        "if test -e /nix/store; then printf 'host_store_exists=true\\n'; "
        "printf 'host_store_stat=%s\\n' \"$(stat -c '%d:%i:%u:%a' /nix/store)\"; "
        "else printf 'host_store_exists=false\\n'; fi"
    ).stdout.decode().splitlines()
    values = dict(line.split("=", 1) for line in result)
    snapshot = {
        "uid": int(values["uid"]),
        "mount_namespace": values["mount_namespace"],
        "mountinfo_sha256": values["mountinfo_sha256"],
        "host_store_exists": values["host_store_exists"] == "true",
    }
    if snapshot["host_store_exists"]:
        snapshot["host_store_stat"] = values["host_store_stat"]
    return snapshot


def elapsed(call):
    started = time.monotonic()
    value = call()
    return value, time.monotonic() - started


def launch_clean_environment(backend, closure, run_id, argv=()):
    name = "NIXSCI_UNDECLARED_CANARY"
    if name in os.environ:
        raise RuntimeError(f"controller already defines {name}")
    os.environ[name] = f"host-only-{time.time_ns():x}"
    try:
        return backend.launch(
            closure,
            run_id=run_id,
            argv=argv,
            env={"NIXSCI_DECLARED_VALUE": "rootless-declared-value"},
        )
    finally:
        del os.environ[name]


def wait_for_proof(backend, handle, timeout):
    started = time.monotonic()
    while not backend.exists(handle, "rootless-proof.json"):
        if not backend.alive(handle):
            log = backend.read_file(handle, "process.log")
            raise RuntimeError(
                f"proof process exited before publishing evidence: {handle!r}; process.log={log!r}"
            )
        if time.monotonic() - started >= timeout:
            raise TimeoutError(f"proof did not finish within {timeout} seconds")
        time.sleep(0.1)
    text = backend.read_file(handle, "rootless-proof.json")
    if text is None:
        raise RuntimeError("proof disappeared before it could be read")
    value = json.loads(text)
    if value["status"] != "pass":
        raise RuntimeError(f"sandbox proof did not pass: {value!r}")
    return value


def wait_until_dead(backend, handle, timeout):
    started = time.monotonic()
    while backend.alive(handle):
        if time.monotonic() - started >= timeout:
            return False, time.monotonic() - started
        time.sleep(0.1)
    return True, time.monotonic() - started


def fetch_json(backend, handle, name):
    with tempfile.TemporaryDirectory(prefix="nixsci-rootless-fetch-") as directory:
        target = Path(directory) / name
        backend.fetch(handle, name, target)
        raw = target.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("native", "ssh"))
    parser.add_argument("--system", default="x86_64-linux")
    parser.add_argument("--store", help="private store root on the target")
    parser.add_argument("--run-root", help="private run root on the target")
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--closure-json", help="controller-resolved tiny and proof closures")
    parser.add_argument("--host")
    parser.add_argument("--remote-bootstrap")
    parser.add_argument("--ssh-option", action="append", default=[])
    parser.add_argument("--jump")
    parser.add_argument("--ready-timeout", type=float, default=0)
    args = parser.parse_args()

    ignore = (".tmp/rootless-experiments/*",)
    if args.resolve_only:
        if not args.closure_json:
            parser.error("--resolve-only requires --closure-json")
        tiny_closure = resolve(".", "hello", args.system, ignore=ignore)
        closure = resolve(".", "namespace-proof", args.system, ignore=ignore)
        record = {
            "tiny": tiny_closure.as_dict(),
            "proof": closure.as_dict(),
        }
        target = Path(args.closure_json)
        target.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        target.chmod(0o644)
        print(json.dumps({"status": "resolved", "path": str(target), "source": closure.source}, sort_keys=True))
        return
    if not args.backend or not args.store or not args.run_root:
        parser.error("target runs require --backend, --store and --run-root")
    if args.closure_json:
        record = json.loads(Path(args.closure_json).read_text())
        tiny_closure = Closure.from_dict(record["tiny"])
        closure = Closure.from_dict(record["proof"])
    else:
        tiny_closure = resolve(".", "hello", args.system, ignore=ignore)
        closure = resolve(".", "namespace-proof", args.system, ignore=ignore)
    if args.backend == "native":
        backend = Native(
            system=args.system,
            store=args.store,
            run_root=args.run_root,
            rootless=True,
        )
        before = local_host_snapshot()
    else:
        if not args.host or not args.remote_bootstrap:
            parser.error("ssh target requires --host and --remote-bootstrap")
        backend = SSH(
            host=args.host,
            system=args.system,
            store=args.store,
            run_root=args.run_root,
            remote_bootstrap=args.remote_bootstrap,
            rootless=True,
            ssh_options=tuple(args.ssh_option),
            jump=args.jump,
            ready_timeout=args.ready_timeout,
        )
        before = remote_host_snapshot(backend)

    tiny_stage, tiny_stage_seconds = elapsed(lambda: backend.stage(tiny_closure))
    if tiny_stage["closure"] != dict(tiny_closure.closure):
        raise RuntimeError("tiny closure NAR map differs from the controller closure")
    tiny_reuse, tiny_reuse_seconds = elapsed(lambda: backend.stage(tiny_closure))
    if tiny_reuse["closure"] != dict(tiny_closure.closure):
        raise RuntimeError("reused tiny closure NAR map differs from the controller closure")
    tiny_id = f"ordinary-{time.time_ns():x}"
    ordinary = None
    ordinary_stop_attempted = False
    ordinary_remove_attempted = False
    try:
        ordinary, ordinary_launch_seconds = elapsed(
            lambda: launch_clean_environment(backend, tiny_closure, tiny_id)
        )
        ordinary_dead, ordinary_runtime_seconds = wait_until_dead(backend, ordinary, 60)
        if not ordinary_dead:
            ordinary_stop_attempted = True
            backend.stop(ordinary)
            raise TimeoutError("ordinary payload did not exit within 60 seconds")
        ordinary_log = backend.read_file(ordinary, "process.log")
        if ordinary_log is None or (
            f"workdir={ordinary['workdir']}" not in ordinary_log
            or "declared: rootless-declared-value" not in ordinary_log
            or "undeclared: present" in ordinary_log
        ):
            raise RuntimeError(f"ordinary payload saw the wrong cwd or environment: {ordinary_log!r}")
        with tempfile.TemporaryDirectory(prefix="nixsci-rootless-ordinary-") as directory:
            fetched_ordinary = Path(directory) / "process.log"
            backend.fetch(ordinary, "process.log", fetched_ordinary)
            if fetched_ordinary.read_text() != ordinary_log:
                raise RuntimeError("fetched ordinary payload log differs from the target log")
        ordinary_remove_attempted = True
        backend.remove(ordinary)
        if backend.exists(ordinary, "."):
            raise RuntimeError("ordinary payload workdir survived remove")
    finally:
        if ordinary is not None:
            if backend.alive(ordinary):
                if not ordinary_stop_attempted:
                    ordinary_stop_attempted = True
                    backend.stop(ordinary)
                dead, _ = wait_until_dead(backend, ordinary, 35)
                if not dead:
                    raise TimeoutError("ordinary payload remained alive during teardown")
            if not ordinary_remove_attempted:
                ordinary_remove_attempted = True
                backend.remove(ordinary)

    stage, stage_seconds = elapsed(lambda: backend.stage(closure))
    if stage["closure"] != dict(closure.closure):
        raise RuntimeError("staged NAR map differs from the controller closure")
    reused, reuse_seconds = elapsed(lambda: backend.stage(closure))
    if reused["closure"] != dict(closure.closure):
        raise RuntimeError("reused NAR map differs from the controller closure")

    metrics = {
        "tiny_stage_seconds": tiny_stage_seconds,
        "tiny_reuse_seconds": tiny_reuse_seconds,
        "ordinary_launch_seconds": ordinary_launch_seconds,
        "ordinary_runtime_seconds": ordinary_runtime_seconds,
        "stage_seconds": stage_seconds,
        "reuse_seconds": reuse_seconds,
    }
    failed_id = f"failed-{time.time_ns():x}"
    failed = {"workdir": f"{backend.run_root}/{failed_id}"}
    failed_remove_attempted = False
    try:
        try:
            backend.launch(closure, run_id=failed_id, program="undeclared-command")
        except KeyError as error:
            metrics["failed_launch"] = {"exception": type(error).__name__, "message": str(error)}
        else:
            raise RuntimeError("undeclared launch program was accepted")
        if not backend.exists(failed, "inputs.json"):
            raise RuntimeError("failed launch did not leave its expected pre-spawn workdir")
        if backend.exists(failed, "pid"):
            raise RuntimeError("failed launch created a child process identity")
        failed_remove_attempted = True
        backend.remove(failed)
        if backend.exists(failed, "."):
            raise RuntimeError("failed launch workdir survived remove")
    finally:
        if not failed_remove_attempted and backend.exists(failed, "."):
            failed_remove_attempted = True
            backend.remove(failed)

    hold_id = f"hold-{time.time_ns():x}"
    held = None
    stop_attempted = False
    held_remove_attempted = False
    try:
        held, launch_seconds = elapsed(
            lambda: launch_clean_environment(backend, closure, hold_id, ("--hold",))
        )
        metrics["hold_launch_seconds"] = launch_seconds
        proof = wait_for_proof(backend, held, 120)
        fetched_proof, fetched_hash = fetch_json(backend, held, "rootless-proof.json")
        if fetched_proof != proof:
            raise RuntimeError("fetched proof differs from the target proof")
        metrics["fetched_proof_sha256"] = fetched_hash
        metrics["process_identity"] = {
            "pid": held["pid"],
            "pgid": held["pgid"],
            "start_time": held["start_time"],
        }
        stop_attempted = True
        _, stop_seconds = elapsed(lambda: backend.stop(held))
        dead, termination_seconds = wait_until_dead(backend, held, 10)
        if not dead:
            raise TimeoutError("SIGTERM did not terminate the held process group within 10 seconds")
        held_remove_attempted = True
        backend.remove(held)
        if backend.exists(held, "."):
            raise RuntimeError("stopped job workdir survived remove")
        metrics["stop_seconds"] = stop_seconds
        metrics["termination_seconds"] = termination_seconds
        metrics["held_job_removed"] = True
    finally:
        if held is not None:
            if backend.alive(held):
                if not stop_attempted:
                    backend.stop(held)
                dead, _ = wait_until_dead(backend, held, 35)
                if not dead:
                    raise TimeoutError("owned proof process remained alive during teardown")
            if not held_remove_attempted:
                held_remove_attempted = True
                backend.remove(held)

    complete_id = f"complete-{time.time_ns():x}"
    complete = None
    complete_stop_attempted = False
    complete_remove_attempted = False
    try:
        complete, launch_seconds = elapsed(
            lambda: launch_clean_environment(backend, closure, complete_id)
        )
        metrics["complete_launch_seconds"] = launch_seconds
        complete_proof = wait_for_proof(backend, complete, 120)
        dead, runtime_seconds = wait_until_dead(backend, complete, 120)
        if not dead:
            raise TimeoutError("completed proof process remained alive")
        log = backend.read_file(complete, "process.log")
        if log is None or "\"status\": \"pass\"" not in log:
            raise RuntimeError(f"completed proof log lacks its pass record: {log!r}")
        fetched_log = None
        with tempfile.TemporaryDirectory(prefix="nixsci-rootless-log-") as directory:
            target = Path(directory) / "process.log"
            backend.fetch(complete, "process.log", target)
            fetched_log = target.read_text()
        if fetched_log != log:
            raise RuntimeError("fetched process log differs from the target log")
        metrics["complete_runtime_seconds"] = runtime_seconds
        metrics["complete_proof"] = complete_proof
        metrics["fetched_process_log_sha256"] = hashlib.sha256(fetched_log.encode()).hexdigest()
        complete_remove_attempted = True
        backend.remove(complete)
        if backend.exists(complete, "."):
            raise RuntimeError("completed job workdir survived remove")
    finally:
        if complete is not None:
            if backend.alive(complete):
                if not complete_stop_attempted:
                    complete_stop_attempted = True
                    backend.stop(complete)
                dead, _ = wait_until_dead(backend, complete, 35)
                if not dead:
                    raise TimeoutError("owned completed proof process remained alive during teardown")
            if not complete_remove_attempted:
                complete_remove_attempted = True
                backend.remove(complete)

    after = local_host_snapshot() if args.backend == "native" else remote_host_snapshot(backend)
    if before != after:
        raise RuntimeError(f"host store or mount view changed during deployment: {before!r} -> {after!r}")
    output = {
        "status": "pass",
        "backend": args.backend,
        "host": args.host if args.backend == "ssh" else "localhost",
        "system": args.system,
        "source": closure.source,
        "tiny_path": tiny_closure.path,
        "tiny_nar_map": dict(tiny_closure.closure),
        "path": closure.path,
        "nar_map": dict(closure.closure),
        "bootstrap_sha256": backend.bootstrap_sha256,
        "private_store_root": backend.store,
        "host_before": before,
        "host_after": after,
        "tiny_stage": tiny_stage,
        "tiny_reuse": tiny_reuse,
        "stage": stage,
        "reuse": reused,
        "metrics_seconds": metrics,
    }
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
