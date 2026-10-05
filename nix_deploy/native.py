from __future__ import annotations
import os, shutil, signal, socket, subprocess, json
from pathlib import Path
from typing import Any, Mapping
from . import nix
from .backend import Backend
from .model import Closure
class Native(Backend):
    def __init__(self, *, system: str, store: str = "/nix/store", run_root: str, nix_executable: str = "nix", rootless: bool = False, bootstrap: str | None = None, bootstrap_sha256: str | None = None, driver: str | None = None, profiles: tuple[str, ...] = ()):
        super().__init__(system=system, store=store, run_root=run_root, nix=nix_executable, rootless=rootless, bootstrap=bootstrap, bootstrap_sha256=bootstrap_sha256, driver=driver, profiles=profiles); Path(run_root).mkdir(parents=True, exist_ok=True); self.nix = shutil.which(self.nix) or self.nix
    def stage(self, closure: Closure) -> dict[str, Any]:
        self._admit(closure)
        if self.rootless: nix.copy_to(self.nix, self.store, closure.path)
        self._verify(closure); return {"path": closure.path, "closure": dict(closure.closure), "system": closure.system}
    def _path(self, workdir: str, relpath: str = "") -> Path:
        root, path = Path(self.run_root).resolve(), (Path(workdir) / relpath).resolve()
        if path != root and root not in path.parents: raise ValueError("handle path escapes the declared run root")
        return path
    def _stage_inputs(self, workdir, records, existing=False):
        root = self._path(workdir); root.mkdir(mode=0o700, parents=False, exist_ok=existing); input_root = root / "inputs"; input_root.mkdir(mode=0o700, exist_ok=existing); env, result = {}, []
        for name, source, digest in records:
            target = input_root / name
            if isinstance(source, bytes): target.write_bytes(source)
            elif source.is_dir(): shutil.copytree(source, target, symlinks=True, dirs_exist_ok=existing)
            else: shutil.copy2(source, target)
            env[f"NIX_DEPLOY_INPUT_{name}"] = str(target); result.append({"name": name, "path": str(target), "sha256": digest})
        return env, result
    def _spawn(self, closure, workdir, run_id, executable, argv, env):
        command = nix.command(self.nix, self._target_store(), "shell", "--offline", closure.path, "--command", executable, *argv); log = self._path(workdir, "process.log").open("ab")
        process = subprocess.Popen(command, cwd=workdir, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=dict(env), start_new_session=True)
        pgid, start = self._identity(process.pid)
        return {"pid": process.pid, "pgid": pgid, "start_time": start}
    def _write_json(self, workdir, name, value): self._path(workdir, name).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    def _read_bytes(self, handle, relpath, offset, missing=False):
        try:
            with self._path(str(handle["workdir"]), relpath).open("rb") as stream:
                if offset is not None:
                    if offset < 0: raise ValueError("offset must be non-negative")
                    stream.seek(offset)
                return stream.read()
        except FileNotFoundError:
            if missing: return None
            return b""
    def _fetch(self, handle, relpath, target):
        source = self._path(str(handle["workdir"]), relpath)
        if source.is_dir(): shutil.copytree(source, target, dirs_exist_ok=True)
        else: target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
    def _exists(self, handle, relpath): return self._path(str(handle["workdir"]), relpath).exists()
    def _identity(self, pid):
        try:
            rest = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if rest[0] == "Z": raise RuntimeError(f"process {pid} exited before its identity was recorded")
            return int(rest[2]), rest[19]
        except (FileNotFoundError, IndexError, ValueError) as error: raise RuntimeError(f"cannot record process identity for {pid}") from error
    def alive(self, handle):
        pid, pgid, start = handle.get("pid"), handle.get("pgid"), str(handle.get("start_time"))
        if not isinstance(pid, int) or not isinstance(pgid, int) or pid <= 0 or pgid <= 0: raise ValueError("native handle has no process identity")
        try: current_pgid, current_start = self._identity(pid)
        except RuntimeError: return False
        if current_pgid != pgid or current_start != start: return False
        try: os.killpg(pgid, 0); return True
        except ProcessLookupError: return False
    def stop(self, handle):
        if not self.alive(handle): raise RuntimeError(f"process {handle.get('pid', '?')} is no longer running")
        os.killpg(int(handle["pgid"]), signal.SIGTERM)
    def remove(self, handle): shutil.rmtree(self._path(str(handle["workdir"])))
    def enter(self, closure): return nix.command(self.nix, self._target_store(), "shell", "--offline", closure.path, "--command")
    def tunnel(self, handle, remote_port, host="127.0.0.1"):
        if host not in ("127.0.0.1", "localhost"): raise ValueError("a native target only has itself")
        if not isinstance(remote_port, int) or not 1 <= remote_port <= 65535: raise ValueError("remote_port must be a TCP port")
        try: socket.create_connection(("127.0.0.1", remote_port), timeout=.5).close()
        except OSError as error: raise RuntimeError(f"native port {remote_port} is not ready") from error
        return remote_port, lambda: None
__all__ = ["Native"]
