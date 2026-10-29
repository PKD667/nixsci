from __future__ import annotations
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import tempfile
import time
from typing import Any, Mapping
from . import nix
from .backend import Backend
from .model import Closure


def _argv(name: str, value) -> tuple[str, ...]:
    """The ssh (or scp) executable, as configuration spells it: a word list, or one string that
    is split like a shell would. A bare string must not become a tuple of its letters."""
    words = tuple(shlex.split(value)) if isinstance(value, str) else tuple(value)
    if not words or not all(isinstance(w, str) and w for w in words):
        raise ValueError(f"{name} must be an executable, optionally followed by arguments")
    return words


# Staging a multi-host job from its first host (`stage = "fanout"`). Used by SSH.receive.
# Why it works with rootless stores: each export and import runs with that host's own bootstrap
# against that host's own store, under the user's own privileges. The stream is Nix's export
# format; a corrupt or incomplete copy fails the NAR-hash check on the receiving host, so the
# first host is trusted for transport only, never for identity.

def _pipe(source: Any, setup: str, producer: str, peer: str, rsh: str, consumer: str, cleanup: str) -> None:
    """On the source host: `setup`, then `producer | rsh peer consumer`, then `cleanup`.

    The exit status of the pipe is the consumer's; cleanup runs either way.
    """
    source._remote(
        f"{setup}; {producer} | {rsh} {shlex.quote(peer)} {shlex.quote(consumer)}; "
        f"rc=$?; {cleanup}; exit $rc"
    )


def _frontend(directory: str, store: str, *args: str) -> str:
    """A `nix-store` frontend (the bootstrap under that name) with the store's flags, as shell text."""
    argv = nix.command(f"{directory}/nix-store", store, *args)
    return f'"{directory}/nix-store"' + "".join(f" {shlex.quote(a)}" for a in argv[1:])


class SSH(Backend):
    def __init__(
        self,
        *,
        host: str,
        system: str,
        store: str,
        run_root: str,
        bootstrap: str | None = None,
        bootstrap_sha256: str | None = None,
        remote_bootstrap: str,
        nix_executable: str = "nix",
        rootless: bool = True,
        ssh_options: tuple[str, ...] = (),
        ssh_command: tuple[str, ...] = ("ssh",),
        scp_command: tuple[str, ...] = ("scp", "-q"),
        jump: str | None = None,
        rsh: str | None = None,
        ready_timeout: float = 0,
        driver: str | None = None,
        profiles: tuple[str, ...] = (),
        stage: str = "direct",
    ):
        if stage not in ("direct", "fanout"):
            raise ValueError("stage must be 'direct' or 'fanout'")
        self.stage_mode = stage
        if not remote_bootstrap.startswith("/"):
            raise ValueError("remote_bootstrap must be an absolute declared target path")
        if remote_bootstrap == "/nix" or remote_bootstrap.startswith("/nix/"):
            raise ValueError("remote_bootstrap must not use the host /nix tree")
        super().__init__(
            system=system,
            store=store,
            run_root=run_root,
            nix=nix_executable,
            rootless=rootless,
            bootstrap=bootstrap,
            bootstrap_sha256=bootstrap_sha256,
            driver=driver,
            profiles=profiles,
        )
        self.host, self.remote_bootstrap = host, remote_bootstrap
        ssh_command, scp_command = _argv("ssh_command", ssh_command), _argv("scp_command", scp_command)
        if jump is not None and not re.fullmatch(r"[A-Za-z0-9_.@,:-]+", jump):
            raise ValueError("jump must be host or user@host entries joined by commas")
        # ssh_command / scp_command let a site use its own wrapper (a batch-system login wrapper, mosh-less proxies...).
        self.ssh_options = (*(() if jump is None else ("-o", f"ProxyJump={jump}")), *ssh_options)
        self._ssh = (
            *ssh_command,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            *self.ssh_options,
        )
        self._scp = (*scp_command, *self.ssh_options)
        self.rsh, self.ready_timeout, self._ready = rsh, float(ready_timeout), False
        self._bootstrap_shipped = False

    def _wait_ready(self) -> None:
        """Hosts that were just allocated or booted may refuse ssh for a while: wait, once."""
        if self._ready or not self.ready_timeout:
            return
        deadline = time.monotonic() + self.ready_timeout
        while subprocess.run(
            [*self._ssh, self.host, "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{self.host} did not accept ssh within {self.ready_timeout:.0f}s"
                )
            time.sleep(2)
        self._ready = True

    def _remote(
        self, command: str, *, data: bytes | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            [*self._ssh, self.host, command],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and result.returncode:
            raise RuntimeError(
                f"SSH command failed: {command[:240]}\n{result.stderr.decode(errors='replace')[-2000:]}"
            )
        if result.returncode == 255:
            raise RuntimeError(f"SSH link failed: {self.host}")
        return result

    def _remote_nix(
        self, *args: str, check: bool = True, data: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        command = shlex.join(nix.command(self.remote_bootstrap, self.store, *args))
        return self._remote(command, data=data, check=check)

    def _ship_bootstrap(self) -> None:
        if self._bootstrap_shipped:
            return
        parent = str(Path(self.remote_bootstrap).parent)
        self._remote(f"umask 077; mkdir -m 700 -p {shlex.quote(parent)}")
        result = subprocess.run(
            [*self._scp, self.nix, f"{self.host}:{self.remote_bootstrap}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise RuntimeError(
                f"cannot ship Nix bootstrap: {result.stderr.decode(errors='replace')[-2000:]}"
            )
        self._check_bootstrap()
        self._bootstrap_shipped = True

    def _check_bootstrap(self) -> None:
        check = (
            self._remote(
                f"chmod 700 {shlex.quote(self.remote_bootstrap)}; sha256sum {shlex.quote(self.remote_bootstrap)}; uname -s; uname -m"
            )
            .stdout.decode()
            .splitlines()
        )
        expected_arch = {"x86_64-linux": "x86_64", "aarch64-linux": "aarch64"}[self.system]
        if (
            len(check) != 3
            or not check[0].startswith(str(self.bootstrap_sha256) + " ")
            or check[1] != "Linux"
            or check[2] != expected_arch
        ):
            raise RuntimeError(f"remote Nix bootstrap verification failed: {check!r}")

    def receive(self, closure: Closure, *, source: "SSH", peer: str, rsh: str) -> dict[str, Any]:
        """Make `closure` present and verified here, copying from `source` (the job's first host).
        Only the transport goes through `source`; identity is checked here, against NAR hashes."""
        self._admit(closure)
        self._wait_ready()
        if not self._bootstrap_shipped:
            parent = str(Path(self.remote_bootstrap).parent)
            _pipe(
                source,
                "true",
                f"cat {shlex.quote(source.remote_bootstrap)}",
                peer,
                rsh,
                f"umask 077; mkdir -p {shlex.quote(parent)}; cat > {shlex.quote(self.remote_bootstrap)}; "
                f"chmod 700 {shlex.quote(self.remote_bootstrap)}",
                "true",
            )
            self._check_bootstrap()
            self._bootstrap_shipped = True
        cached = self._remote_hashes()
        for path, expected in closure.closure.items():
            if path in cached and cached[path] != expected:
                raise RuntimeError(f"preexisting target path NAR identity differs: {path}")
        missing = [path for path in closure.closure if path not in cached]
        if missing:
            src_stage, dst_stage = f"{source.run_root}/.stage", f"{self.run_root}/.stage"
            _pipe(
                source,
                f"umask 077; mkdir -p {shlex.quote(src_stage)}; "
                f"d=$(mktemp -d {shlex.quote(src_stage)}/fanout-XXXXXXXX); "
                f"ln -s {shlex.quote(source.remote_bootstrap)} \"$d/nix-store\"",
                _frontend("$d", source.store, "--export", *missing),
                peer,
                rsh,
                f"umask 077; mkdir -p {shlex.quote(dst_stage)}; "
                f"d=$(mktemp -d {shlex.quote(dst_stage)}/fanout-XXXXXXXX); "
                f"ln -s {shlex.quote(self.remote_bootstrap)} \"$d/nix-store\"; "
                f"{_frontend('$d', self.store, '--import')}; rm -rf -- \"$d\"",
                'rm -rf -- "$d"',
            )
        self._verify(closure)
        return {"path": closure.path, "closure": dict(closure.closure), "system": closure.system}

    def _remote_hashes(self, root: str | None = None) -> dict[str, str]:
        # --all enumerates registered paths without probing missing paths (a
        # nonzero path-info result is always an error, never a cache miss).
        args = ["--all"] if root is None else ["--recursive"]
        raw = self._remote_nix("path-info", *args, "--json", *([] if root is None else [root])).stdout
        try:
            entries = json.loads(raw)
        except ValueError as error:
            raise RuntimeError("remote Nix path-info was not JSON") from error
        if isinstance(entries, dict):
            entries = [{"path": path, **info} for path, info in entries.items()]
        if not isinstance(entries, list):
            raise RuntimeError("remote Nix path-info did not return a list or path map")
        result = {}
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("narHash"), str)
            ):
                raise RuntimeError(f"remote Nix path-info entry lacks NAR identity: {entry!r}")
            if entry["path"] in result and result[entry["path"]] != entry["narHash"]:
                raise RuntimeError("remote Nix path-info has conflicting NAR identities")
            result[entry["path"]] = entry["narHash"]
        if root is not None and root not in result:
            raise RuntimeError(f"remote closure does not contain {root}")
        return result

    def _verify(self, closure: Closure) -> None:
        self._remote_nix("store", "verify", "--recursive", "--no-trust", closure.path)
        if self._remote_hashes(closure.path) != dict(closure.closure):
            raise RuntimeError("target closure NAR identity differs from the resolved closure")

    def stage(self, closure: Closure) -> dict[str, Any]:
        self._admit(closure)
        self._wait_ready()
        remote_uid = self._remote("id -u").stdout.decode().strip()
        if not remote_uid.isdecimal() or int(remote_uid) == 0:
            raise RuntimeError(f"rootless SSH targets require a non-root host user, got {remote_uid!r}")
        for target_path in (self.store, self.run_root, self.remote_bootstrap):
            resolved = self._remote(
                f"realpath -m -- {shlex.quote(target_path)}"
            ).stdout.decode().strip()
            if resolved == "/nix" or resolved.startswith("/nix/"):
                raise RuntimeError(f"private target path resolves inside the host /nix tree: {resolved}")
        self._ship_bootstrap()
        # Private store root: created 0700 and owned by this (non-root) user.
        self._remote(f"umask 077; mkdir -m 700 -p {shlex.quote(self.store)}")
        identity = self._remote(
            f"stat -c '%u %a' {shlex.quote(self.store)}; id -u"
        ).stdout.decode().splitlines()
        if len(identity) != 2 or identity[0] != identity[1] + " 700":
            raise RuntimeError(f"remote private store root is not owned mode 700: {identity!r}")
        cached = self._remote_hashes()
        missing = []
        for path, expected in closure.closure.items():
            if path not in cached:
                missing.append(path)
            elif cached[path] != expected:
                raise RuntimeError(f"preexisting target path NAR identity differs: {path}")
        if not missing:
            self._verify(closure)
            return {"path": closure.path, "closure": dict(closure.closure), "system": closure.system}
        token = closure.path.rsplit("/", 1)[-1].split("-", 1)[0]
        with tempfile.TemporaryDirectory(prefix="nix-deploy-cache-") as temp:
            archive = str(Path(temp) / "closure.export")
            # Native export supports a partial graph. A file binary cache does
            # not: it refuses paths whose already-cached references are absent
            # from that cache. Never invent narinfo records to bypass that check.
            nix.export_paths(self.nix, archive, *missing)
            # Stage on the declared target disk, not /tmp (often small tmpfs).
            # Each invocation owns a unique directory: concurrent staging of
            # the same closure must not remove another invocation's cache.
            stage_root = f"{self.run_root}/.stage"
            parent = self._remote(
                f"umask 077; mkdir -p {shlex.quote(stage_root)}; "
                f"mktemp -d {shlex.quote(stage_root + '/nar-' + token + '-XXXXXXXX')}"
            ).stdout.decode().strip()
            if not re.fullmatch(re.escape(stage_root + "/nar-" + token + "-") + r"[A-Za-z0-9]{8,}", parent):
                raise RuntimeError("remote staging directory is outside the declared run root")
            try:
                result = subprocess.run(
                    [*self._scp, "-C", archive, f"{self.host}:{parent}/"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.returncode:
                    raise RuntimeError(
                        f"cannot ship closure cache: {result.stderr.decode(errors='replace')[-2000:]}"
                    )
                # argv[0] selects the static Nix binary's legacy import frontend.
                # This alias lives only inside this invocation's private stage.
                importer = f"{parent}/nix-store"
                self._remote(f"ln -s {shlex.quote(self.remote_bootstrap)} {shlex.quote(importer)}")
                command = nix.command(importer, self.store, "--import")
                self._remote(shlex.join(command) + " < "
                             + shlex.quote(f"{parent}/{Path(archive).name}"))
            finally:
                self._remote(f"rm -rf -- {shlex.quote(parent)}")
        self._verify(closure)
        return {"path": closure.path, "closure": dict(closure.closure), "system": closure.system}

    def _workdir_path(self, workdir: str, relpath: str = "") -> str:
        if not workdir.startswith(self.run_root + "/") or ".." in Path(workdir).parts:
            raise ValueError("handle path escapes the declared remote run root")
        if relpath and (relpath.startswith("/") or ".." in Path(relpath).parts):
            raise ValueError("path escapes the remote workdir")
        return workdir.rstrip("/") + ("/" + relpath if relpath else "")

    def _stage_inputs(
        self, workdir: str, records: list[tuple[str, Any, str]], existing: bool = False
    ) -> tuple[dict[str, str], list[dict[str, str]]]:
        self._remote(f"mkdir -m 700 -p {shlex.quote(self._workdir_path(workdir, 'inputs'))}")
        env, manifest = {}, []
        for name, source, digest in records:
            target = self._workdir_path(workdir, f"inputs/{name}")
            if isinstance(source, bytes):
                self._remote(f"umask 077; cat > {shlex.quote(target)}", data=source)
            else:
                result = subprocess.run(
                    [
                        *self._scp,
                        *(["-rp"] if source.is_dir() else []),
                        str(source),
                        f"{self.host}:{target}",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.returncode:
                    raise RuntimeError(
                        f"cannot ship private input {name!r}: {result.stderr.decode(errors='replace')[-1000:]}"
                    )
            if not isinstance(source, bytes) and source.is_dir():
                actual = (
                    self._remote(
                        "tar -C "
                        + shlex.quote(target)
                        + " --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - . | sha256sum"
                    )
                    .stdout.decode()
                    .split()[0]
                )
            else:
                actual = self._remote(f"sha256sum {shlex.quote(target)}").stdout.decode().split()[0]
            if actual != digest:
                raise RuntimeError(f"remote private input {name!r} hash mismatch")
            env[f"NIX_DEPLOY_INPUT_{name}"] = target
            manifest.append({"name": name, "path": target, "sha256": digest})
        return env, manifest

    def _spawn(
        self,
        closure: Closure,
        workdir: str,
        run_id: str,
        executable: str,
        argv: tuple[str, ...],
        env: Mapping[str, str],
    ) -> dict[str, Any]:
        self._remote(f"mkdir -m 700 -p {shlex.quote(workdir)}")
        pid_file = self._workdir_path(workdir, "pid")
        values = {**env}
        command = nix.command(
            self.remote_bootstrap,
            self.store,
            "shell",
            "--offline",
            closure.path,
            "--command",
            executable,
            *argv,
        )
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(values.items()))
        script = (
            '#!/bin/sh\nset -eu\nstat=$(cat /proc/$$/stat); rest=${stat##*) }; set -- $rest; printf \'%s %s %s\\n\' "$$" "$3" "${20}" > '
            + shlex.quote(pid_file)
            + "\ncd "
            + shlex.quote(workdir)
            + "\nexec env -i "
            + ("" if "HOME=" in exports else 'HOME="$HOME" ')
            + exports
            + " "
            + shlex.join(command)
            + " > "
            + shlex.quote(self._workdir_path(workdir, "process.log"))
            + " 2>&1 < /dev/null\n"
        )
        script_path = self._workdir_path(workdir, "launch.sh")
        self._remote(
            f"umask 077; cat > {shlex.quote(script_path)}; chmod 700 {shlex.quote(script_path)}",
            data=script.encode(),
        )
        # -f backgrounds the SSH client after authentication; the remote script
        # itself stays foreground, so no inherited SSH channel keeps the run alive.
        result = subprocess.run(
            [*self._ssh, "-f", "-n", self.host, "setsid", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise RuntimeError(
                f"cannot start remote run: {result.stderr.decode(errors='replace')[-2000:]}"
            )
        identity = None
        for _ in range(50):
            result = self._remote(f"cat {shlex.quote(pid_file)}", check=False)
            if result.returncode == 0 and len(result.stdout.split()) == 3:
                identity = tuple(result.stdout.decode().split())
                break
            time.sleep(0.1)
        if identity is None:
            raise RuntimeError("remote run did not publish its process identity")
        return {
            "pid": int(identity[0]),
            "pgid": int(identity[1]),
            "start_time": identity[2],
            "pid_file": pid_file,
        }

    def _write_json(self, workdir: str, name: str, value: Any) -> None:
        self._remote(
            f"umask 077; cat > {shlex.quote(self._workdir_path(workdir, name))}",
            data=(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )

    def _read_bytes(
        self, handle: Mapping[str, Any], relpath: str, offset: int | None, missing: bool = False
    ) -> bytes | None:
        path = self._workdir_path(str(handle["workdir"]), relpath)
        command = (
            f"tail -c +{offset + 1} {shlex.quote(path)}"
            if offset is not None
            else f"cat {shlex.quote(path)}"
        )
        result = self._remote(command, check=False)
        if result.returncode and not (missing and result.returncode == 1):
            raise RuntimeError(f"cannot read remote file {path}")
        return None if result.returncode else result.stdout

    def _fetch(self, handle: Mapping[str, Any], relpath: str, target: Path) -> None:
        remote = self._workdir_path(str(handle["workdir"]), relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [*self._scp, "-r", f"{self.host}:{remote}", str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise RuntimeError(
                f"cannot fetch {relpath!r}: {result.stderr.decode(errors='replace')[-1000:]}"
            )

    def _exists(self, handle: Mapping[str, Any], relpath: str) -> bool:
        return (
            self._remote(
                f"test -e {shlex.quote(self._workdir_path(str(handle['workdir']), relpath))}",
                check=False,
            ).returncode
            == 0
        )

    def alive(self, handle: Mapping[str, Any]) -> bool:
        pid, pgid, start = int(handle["pid"]), int(handle["pgid"]), str(handle["start_time"])
        command = f"test \"$(sed 's/.*) //' /proc/{pid}/stat | awk '{{print $3}}')\" = {pgid} && test \"$(sed 's/.*) //' /proc/{pid}/stat | awk '{{print $20}}')\" = {shlex.quote(start)} && kill -0 -- -{pgid}"
        return self._remote(command, check=False).returncode == 0

    def stop(self, handle: Mapping[str, Any]) -> None:
        if not self.alive(handle):
            raise RuntimeError(f"remote run {handle.get('id', '?')} is not running")
        result = self._remote(f"kill -TERM -- -{int(handle['pgid'])}", check=False)
        if result.returncode:
            raise RuntimeError(f"cannot stop remote run {handle.get('id', '?')}")

    def remove(self, handle: Mapping[str, Any]) -> None:
        self._remote(f"rm -rf -- {shlex.quote(str(handle['workdir']))}")

    def enter(self, closure: Closure) -> list[str]:
        return nix.command(
            self.remote_bootstrap, self.store, "shell", "--offline", closure.path, "--command"
        )

    def tunnel(self, handle: Mapping[str, Any], remote_port: int, host: str = "127.0.0.1"):
        if not isinstance(remote_port, int) or not 1 <= remote_port <= 65535:
            raise ValueError("remote_port must be a TCP port")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", host):
            raise ValueError("tunnel host must be a hostname or address")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            local = sock.getsockname()[1]
        process = subprocess.Popen(
            [
                *self._ssh,
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"127.0.0.1:{local}:{host}:{remote_port}",
                self.host,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"SSH tunnel failed: {process.stderr.read().decode()[-500:]}")
            try:
                socket.create_connection(("127.0.0.1", local), timeout=0.2).close()
                return local, lambda: (process.terminate(), process.wait(timeout=5))
            except OSError:
                time.sleep(0.1)
        process.terminate()
        raise TimeoutError(f"SSH tunnel to {self.host}:{remote_port} did not become ready")


__all__ = ["SSH"]
