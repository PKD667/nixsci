"""Run the bounded grader sandbox from inside the patched Nix store view."""

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sys
import subprocess
import tempfile
import time

CPU_SECONDS = 5
ADDRESS_SPACE = 1024 * 1024 * 1024
FILE_SIZE = 4 * 1024 * 1024
PROCESS_COUNT = 256
OPEN_FILES = 64

CANDIDATE = r'''
#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define CPU_SECONDS @CPU_SECONDS@
#define ADDRESS_SPACE @ADDRESS_SPACE@
#define FILE_SIZE @FILE_SIZE@
#define PROCESS_COUNT @PROCESS_COUNT@
#define OPEN_FILES @OPEN_FILES@

static int limit_is(int which, rlim_t expected) {
    struct rlimit value;
    return getrlimit(which, &value) == 0 && value.rlim_cur == expected;
}

static int process_limit_probe(void) {
    struct rlimit limit = {8, 8};
    pid_t children[16];
    int count = 0, denied = 0;
    if (setrlimit(RLIMIT_NPROC, &limit) != 0) return 20;
    while (count < 16) {
        pid_t child = fork();
        if (child == 0) {
            for (;;) pause();
        }
        if (child < 0) {
            if (errno != EAGAIN) return 21;
            denied = 1;
            break;
        }
        children[count++] = child;
    }
    for (int i = 0; i < count; i++) kill(children[i], SIGKILL);
    for (int i = 0; i < count; i++) waitpid(children[i], NULL, 0);
    return denied ? count : 22;
}

static int cap_eff(char *value, size_t size) {
    FILE *status = fopen("/proc/self/status", "r");
    char line[256];
    if (!status) return -1;
    while (fgets(line, sizeof(line), status)) {
        if (strncmp(line, "CapEff:", 7) == 0) {
            char *p = line + 7;
            while (*p == ' ' || *p == '\t') p++;
            size_t n = strcspn(p, "\r\n");
            if (n >= size) { fclose(status); return -1; }
            memcpy(value, p, n);
            value[n] = '\0';
            fclose(status);
            return 0;
        }
    }
    fclose(status);
    return -1;
}

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "identity") == 0) {
        FILE *status = fopen("/proc/self/status", "r");
        char line[256];
        long host_pid = -1;
        if (!status) return 40;
        while (fgets(line, sizeof(line), status)) {
            if (strncmp(line, "NSpid:", 6) == 0) {
                char *p = line + 6;
                while (*p == ' ' || *p == '\t') p++;
                host_pid = strtol(p, NULL, 10);
                break;
            }
        }
        fclose(status);
        if (host_pid <= 0) return 41;
        FILE *marker = fopen(argv[2], "w");
        if (!marker) return 42;
        fprintf(marker, "%ld\n", host_pid);
        fclose(marker);
        sleep(2);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "output") == 0) {
        char block[65536] = {0};
        for (int i = 0; i < 128; i++) {
            if (write(STDOUT_FILENO, block, sizeof(block)) < 0) return 30;
        }
        return 0;
    }
    if (argc != 3) return 1;
    if (!limit_is(RLIMIT_CPU, CPU_SECONDS) ||
        !limit_is(RLIMIT_AS, ADDRESS_SPACE) ||
        !limit_is(RLIMIT_FSIZE, FILE_SIZE) ||
        !limit_is(RLIMIT_NPROC, PROCESS_COUNT) ||
        !limit_is(RLIMIT_NOFILE, OPEN_FILES)) return 2;

    if (access(argv[1], R_OK) == 0) return 3;

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return 4;
    struct sockaddr_in peer = {0};
    peer.sin_family = AF_INET;
    peer.sin_port = htons((uint16_t)strtoul(argv[2], NULL, 10));
    peer.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int connected = connect(fd, (struct sockaddr *)&peer, sizeof(peer)) == 0;
    close(fd);
    if (connected) return 5;

    size_t request = (size_t)2 * 1024 * 1024 * 1024;
    void *allocation = mmap(NULL, request, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (allocation != MAP_FAILED) {
        munmap(allocation, request);
        return 6;
    }
    if (errno != ENOMEM) return 7;

    int children = process_limit_probe();
    if (children < 1 || children > 7) return 8;

    char caps[64];
    if (cap_eff(caps, sizeof(caps)) != 0) return 9;
    printf("uid=%u\ngid=%u\ncap_eff=%s\nprocesses_before_eagain=%d\n",
           (unsigned)getuid(), (unsigned)getgid(), caps, children);
    FILE *map = fopen("/proc/self/uid_map", "r");
    if (!map || !fgets(caps, sizeof(caps), map)) return 10;
    fclose(map);
    caps[strcspn(caps, "\r\n")] = '\0';
    printf("uid_map=%s\n", caps);
    return 0;
}
'''


def status_value(name):
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"/proc/self/status lacks {name}")


def invoke(prefix, command, cwd, timeout=5):
    started = time.monotonic()
    with tempfile.TemporaryFile(dir=cwd) as out, tempfile.TemporaryFile(dir=cwd) as err:
        try:
            result = subprocess.run(
                prefix + list(command), cwd=cwd, stdout=out, stderr=err, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            result = None
        elapsed = time.monotonic() - started
        out.flush()
        err.flush()
        out_size, err_size = out.tell(), err.tell()
        out.seek(0)
        err.seek(0)
        return {
            "returncode": None if result is None else result.returncode,
            "timed_out": result is None,
            "elapsed_seconds": elapsed,
            "stdout_size_bytes": out_size,
            "stderr_size_bytes": err_size,
            "stdout": out.read().decode("utf-8", "replace"),
            "stderr": err.read().decode("utf-8", "replace"),
        }


def process_status(pid):
    fields = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            fields[name] = value.strip()
    return fields


def inspect_nested_host_identity(prefix, binary, cwd, expected_uid, expected_gid):
    marker = cwd / f"nested-host-pid-{os.getpid()}"
    with tempfile.TemporaryFile(dir=cwd) as out, tempfile.TemporaryFile(dir=cwd) as err:
        process = subprocess.Popen(
            prefix + [str(binary), "identity", str(marker)],
            cwd=cwd,
            stdout=out,
            stderr=err,
        )
        started = time.monotonic()
        try:
            while not marker.exists():
                if process.poll() is not None:
                    raise RuntimeError("nested PID probe exited before publishing its host PID")
                if time.monotonic() - started >= 5:
                    raise TimeoutError("nested PID probe did not publish its host PID within 5 seconds")
                time.sleep(0.05)
            host_pid = int(marker.read_text())
            fields = process_status(host_pid)
            uid = [int(value) for value in fields["Uid"].split()]
            gid = [int(value) for value in fields["Gid"].split()]
            capabilities = fields["CapEff"]
            if uid != [expected_uid] * 4 or gid != [expected_gid] * 4:
                raise RuntimeError(f"nested candidate host credentials changed: uid={uid!r} gid={gid!r}")
            if int(capabilities, 16) != 0:
                raise RuntimeError(f"nested candidate host CapEff is not zero: {capabilities}")
            if process.wait(timeout=5) != 0:
                raise RuntimeError("nested PID probe exited nonzero")
            return {"host_pid": host_pid, "uid": uid, "gid": gid, "cap_eff": capabilities}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if marker.exists():
                marker.unlink()


def leftover_time_probe(token):
    matches = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if token in command:
            matches.append(int(entry.name))
    return matches


def main():
    cwd = Path.cwd()
    declared_environment = os.environ.get("NIXSCI_DECLARED_VALUE")
    expected_uid = int(os.environ["NIXSCI_EXPECTED_HOST_UID"])
    expected_gid = int(os.environ["NIXSCI_EXPECTED_HOST_GID"])
    undeclared_canary_present = "NIXSCI_UNDECLARED_CANARY" in os.environ
    if (
        declared_environment != "rootless-declared-value"
        or undeclared_canary_present
        or os.getuid() != expected_uid
        or os.getgid() != expected_gid
    ):
        raise RuntimeError("payload credentials or environment differed from the declared target")
    tools = {name: shutil.which(name) for name in ("bwrap", "prlimit", "true", "tcc", "python3")}
    for name, path in tools.items():
        if path is None or not path.startswith("/nix/store/"):
            raise RuntimeError(f"rootless grading requires Nix-provisioned {name}: {path!r}")

    root_mount = None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split(" - ", 1)[0].split()
        if fields[4] == "/":
            root_mount = {
                "mount_root": fields[3],
                "mount_point": fields[4],
                "device": fields[2],
                "options": fields[5],
                "propagation": fields[6:],
            }
            break
    if root_mount is None or root_mount["mount_root"] == "/":
        raise RuntimeError(f"execution root is not a pivoted mount root: {root_mount!r}")
    if any(item.startswith(("shared:", "master:", "propagate_from:")) for item in root_mount["propagation"]):
        raise RuntimeError(f"execution root mount propagation is not private: {root_mount!r}")

    outer = {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "uid_status": status_value("Uid"),
        "gid_status": status_value("Gid"),
        "declared_environment_value": declared_environment,
        "undeclared_controller_canary_present": undeclared_canary_present,
        "uid_map": Path("/proc/self/uid_map").read_text().strip(),
        "gid_map": Path("/proc/self/gid_map").read_text().strip(),
        "cap_eff": status_value("CapEff"),
        "user_namespace": os.readlink("/proc/self/ns/user"),
        "mount_namespace": os.readlink("/proc/self/ns/mnt"),
        "root_mount": root_mount,
        "root_stat": [Path("/").stat().st_dev, Path("/").stat().st_ino],
        "proc_root_stat": [Path("/proc/self/root").stat().st_dev, Path("/proc/self/root").stat().st_ino],
        "store_stat": [Path("/nix/store").stat().st_dev, Path("/nix/store").stat().st_ino],
    }
    uid_map = [int(value) for value in outer["uid_map"].split()]
    gid_map = [int(value) for value in outer["gid_map"].split()]
    if int(outer["cap_eff"], 16) != 0:
        raise RuntimeError(f"Nix namespace setup retained effective capabilities: {outer['cap_eff']}")
    if uid_map != [os.getuid(), os.getuid(), 1] or gid_map != [os.getgid(), os.getgid(), 1]:
        raise RuntimeError(f"Nix namespace changed the host UID/GID mapping: {outer!r}")
    if outer["root_stat"] != outer["proc_root_stat"]:
        raise RuntimeError(f"execution root mismatch: {outer!r}")

    prefix = [
        tools["prlimit"],
        f"--cpu={CPU_SECONDS}:{CPU_SECONDS}",
        f"--as={ADDRESS_SPACE}:{ADDRESS_SPACE}",
        f"--fsize={FILE_SIZE}:{FILE_SIZE}",
        f"--nproc={PROCESS_COUNT}:{PROCESS_COUNT}",
        f"--nofile={OPEN_FILES}:{OPEN_FILES}",
        "--",
        tools["bwrap"],
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/nix/store",
        "/nix/store",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(cwd),
        str(cwd),
        "--chdir",
        str(cwd),
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "PATH",
        os.environ["PATH"],
        "--",
    ]
    probe = subprocess.run(prefix + [tools["true"]], capture_output=True, text=True, timeout=5)
    if probe.returncode:
        raise RuntimeError(f"bubblewrap setup failed ({probe.returncode}): {probe.stderr}")

    source = cwd / "candidate.c"
    binary = cwd / "candidate"
    candidate_source = CANDIDATE
    for name, value in (
        ("CPU_SECONDS", CPU_SECONDS),
        ("ADDRESS_SPACE", ADDRESS_SPACE),
        ("FILE_SIZE", FILE_SIZE),
        ("PROCESS_COUNT", PROCESS_COUNT),
        ("OPEN_FILES", OPEN_FILES),
    ):
        candidate_source = candidate_source.replace(f"@{name}@", str(value))
    source.write_text(candidate_source)
    compile_result = invoke(prefix, [tools["tcc"], "-O2", "-o", str(binary), str(source)], cwd)
    if compile_result["returncode"] != 0:
        raise RuntimeError(f"compiler fixture failed in grader sandbox: {compile_result!r}")

    fd, canary_name = tempfile.mkstemp(prefix=f"nixsci-rootless-canary-{os.getuid()}-")
    canary = Path(canary_name)
    marker = None
    try:
        with os.fdopen(fd, "wb") as secret:
            secret.write(b"undeclared-host-canary")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                accepted, _ = listener.accept()
                accepted.close()

            candidate = invoke(prefix, [str(binary), str(canary), str(port)], cwd)
            if candidate["returncode"] != 0:
                raise RuntimeError(f"candidate denial/limit assertions failed: {candidate!r}")
            nested = dict(line.split("=", 1) for line in candidate["stdout"].splitlines())
            if int(nested["cap_eff"], 16) != 0:
                raise RuntimeError(f"bubblewrap retained effective capabilities: {nested!r}")
            mapped = [int(value) for value in nested["uid_map"].split()]
            if len(mapped) != 3 or mapped[0] != int(nested["uid"]) or mapped[2] != 1:
                raise RuntimeError(f"nested namespace has an unexpected UID map: {nested!r}; outer={outer!r}")
            nested_host_identity = inspect_nested_host_identity(
                prefix, binary, cwd, expected_uid, expected_gid
            )

            output = invoke(prefix, [str(binary), "output"], cwd)
            if output["returncode"] == 0 or output["stdout_size_bytes"] != FILE_SIZE:
                raise RuntimeError(f"file/output-size bound was not enforced: {output!r}")

            marker = cwd / f"time-probe-{os.getpid()}"
            token = f"nixsci-rootless-time-{os.getpid()}"
            timed_code = f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('started'); time.sleep(30); print({token!r})"
            timed = invoke(prefix, [tools["python3"], "-c", timed_code], cwd)
            if not timed["timed_out"] or not marker.exists():
                raise RuntimeError(f"wall-clock bound did not stop a started candidate: {timed!r}")
            leftovers = leftover_time_probe(token)
            if leftovers:
                for pid in leftovers:
                    os.kill(pid, signal.SIGKILL)
                raise RuntimeError(f"wall-clock timeout left candidate processes {leftovers!r}")

            enter = os.environ["NIX_DEPLOY_ENTER"].split()
            declared_store = enter[enter.index("--store") + 1]
            store_mount = None
            for line in Path("/proc/self/mountinfo").read_text().splitlines():
                fields = line.split(" - ", 1)[0].split()
                if fields[4] == "/nix/store":
                    store_mount = {
                        "mount_root": fields[3],
                        "device": fields[2],
                        "propagation": fields[6:],
                    }
                    break
            if store_mount is None or not store_mount["mount_root"].endswith("store/nix/store"):
                raise RuntimeError(f"/nix/store is not backed by the declared private store: {store_mount!r}")
            if any(item.startswith(("shared:", "master:", "propagate_from:")) for item in store_mount["propagation"]):
                raise RuntimeError(f"private store mount propagation is not private: {store_mount!r}")
            evidence = {
                "status": "pass",
                "declared_store_root": declared_store,
                "outer": outer,
                "store_mount": store_mount,
                "sandbox": {
                    "probe_returncode": probe.returncode,
                    "candidate": nested,
                    "candidate_host_identity": nested_host_identity,
                    "canary_denied": True,
                    "host_loopback_denied": True,
                    "memory_request_2gib_denied_under_1gib_limit": True,
                    "nproc_lowered_to_8_and_eagain_observed": True,
                    "file_output_limit_bytes": output["stdout_size_bytes"],
                    "time_limit_seconds": timed["elapsed_seconds"],
                    "time_probe_leftovers": leftovers,
                    "tools": tools,
                },
            }
    finally:
        if canary.exists():
            canary.unlink()
        if marker is not None and marker.exists():
            marker.unlink()
    pending = cwd / f"rootless-proof-{os.getpid()}.tmp"
    pending.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    os.replace(pending, cwd / "rootless-proof.json")
    print(json.dumps(evidence, sort_keys=True), flush=True)
    if "--hold" in sys.argv[1:]:
        time.sleep(30)


if __name__ == "__main__":
    main()
