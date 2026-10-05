"""Shared manifest and lifecycle protocol for local and SSH targets."""

from __future__ import annotations
import hashlib, json, os, re, shlex, subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from .model import Closure

#: Host tools (ssh, oarsh, ip) a job may call; closures bring everything else themselves.
HOST_PATH = "/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin"
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_RUN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def normalize_manifest(value: Mapping[str, Any], paths: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("experiment.json must be an object")

    def program(item: object, field: str) -> str:
        if (
            not isinstance(item, str)
            or not item.startswith("/nix/store/")
            or any(x in ("", ".", "..") for x in item.split("/")[4:])
            or not any(item == p or item.startswith(p + "/") for p in paths)
        ):
            raise ValueError(f"{field} must be an executable in the complete store closure")
        return item

    result = dict(value)
    result["program"] = program(value.get("program"), "program")
    commands = value.get("commands", {})
    if not isinstance(commands, Mapping):
        raise ValueError("experiment.json commands must be an object")
    if any(not isinstance(name, str) or not name for name in commands):
        raise ValueError("manifest command names must be strings")
    result["commands"] = {
        name: program(item, f"commands.{name}") for name, item in commands.items()
    }
    argv = value.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(x, str) and "\x00" not in x for x in argv):
        raise ValueError("manifest argv is invalid")
    result["argv"] = list(argv)
    env = value.get("env", {})
    if not isinstance(env, Mapping) or any(
        not isinstance(k, str) or not k or "\x00" in k or not isinstance(v, str) or "\x00" in v
        for k, v in env.items()
    ):
        raise ValueError("manifest env is invalid")
    result["env"] = dict(env)
    resources = value.get("resources", {})
    if not isinstance(resources, Mapping):
        raise ValueError("manifest resources is invalid")
    inputs = value.get("inputs", {})
    if not isinstance(inputs, Mapping):
        raise ValueError("manifest inputs is invalid")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("manifest metadata is invalid")
    result["resources"], result["inputs"], result["metadata"] = (
        dict(resources),
        dict(inputs),
        dict(metadata),
    )
    return result


def _hash_path(path: Path) -> str:
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    if not path.is_dir():
        raise FileNotFoundError(path)
    if any(
        "\n" in str(x.relative_to(path)) or "\x00" in str(x.relative_to(path))
        for x in path.rglob("*")
    ):
        raise ValueError("input paths may not contain NUL or newline")
    result = subprocess.run(
        [
            "tar",
            "-C",
            str(path),
            "--sort=name",
            "--mtime=@0",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "-cf",
            "-",
            ".",
        ],
        stdout=subprocess.PIPE,
        check=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _input_records(inputs: Mapping[str, Any] | None) -> list[tuple[str, Any, str]]:
    if inputs is None:
        return []
    if not isinstance(inputs, Mapping):
        raise TypeError("inputs must be an explicit name-to-path mapping")
    result = []
    for name, item in inputs.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"invalid private input name {name!r}")
        expected = item.get("sha256") if isinstance(item, Mapping) else None
        item = item.get("path", item.get("data")) if isinstance(item, Mapping) else item
        if isinstance(item, (str, os.PathLike)):
            source, actual = Path(item), _hash_path(Path(item))
        elif isinstance(item, bytes):
            source, actual = item, hashlib.sha256(item).hexdigest()
        else:
            raise TypeError(f"input {name!r} must be a path, bytes, or mapping")
        if expected is not None and expected != actual:
            raise ValueError(f"private input {name!r} hash mismatch")
        result.append((name, source, actual))
    return result


def _relative(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\x00" in path
        or ".." in PurePosixPath(path).parts
    ):
        raise ValueError("path must be a non-empty relative path")
    return path


class Backend:
    backend_name = "backend"

    def __init__(
        self,
        *,
        system: str,
        store: str,
        run_root: str,
        nix: str = "nix",
        rootless: bool,
        bootstrap: str | None = None,
        bootstrap_sha256: str | None = None,
        driver: str | None = None,
        profiles: tuple[str, ...] = (),
    ):
        if not store.startswith("/") or not run_root.startswith("/"):
            raise ValueError("store and run_root must be absolute target paths")
        if rootless and (not bootstrap or not bootstrap_sha256):
            raise ValueError("rootless targets require a pinned static Nix bootstrap and SHA-256")
        if not rootless and store != "/nix/store":
            raise ValueError("existing-store targets must declare /nix/store")
        self.system, self.store, self.run_root, self.nix, self.rootless = (
            system,
            store,
            run_root.rstrip("/"),
            nix,
            rootless,
        )
        self.bootstrap, self.bootstrap_sha256, self.driver, self.profiles = (
            bootstrap,
            bootstrap_sha256,
            driver,
            tuple(profiles),
        )
        self._verify_bootstrap(bootstrap, bootstrap_sha256)

    def _verify_bootstrap(self, path: str | None, expected: str | None) -> None:
        if path is None:
            if expected is not None:
                raise ValueError("bootstrap_sha256 requires bootstrap")
            return
        file = Path(path)
        if not file.is_file() or not os.access(file, os.X_OK):
            raise ValueError(f"Nix bootstrap is not executable: {path}")
        if (
            not expected
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or _hash_path(file) != expected
        ):
            raise ValueError(f"Nix bootstrap hash mismatch: {path}")
        self.nix = str(file)

    def _target_store(self) -> str | None:
        return self.store if self.rootless else None

    def _verify(self, closure: Closure) -> None:
        from .nix import verify

        if verify(self.nix, self._target_store(), closure.path) != dict(closure.closure):
            raise RuntimeError("target closure NAR identity differs from the resolved closure")

    def _admit(self, closure: Closure) -> None:
        if closure.system != self.system:
            raise RuntimeError(
                f"closure is built for {closure.system}, this target runs {self.system}"
            )
        resources = closure.metadata.get("resources", {})
        if not isinstance(resources, Mapping):
            raise ValueError("closure resources are not normalized")
        wanted_driver, wanted_profile = resources.get("driver"), resources.get("profile")
        if wanted_driver is not None and wanted_driver != self.driver:
            raise RuntimeError(f"target does not provide declared driver {wanted_driver!r}")
        if wanted_profile is not None and wanted_profile not in self.profiles:
            raise RuntimeError(f"target does not provide declared profile {wanted_profile!r}")

    def stage(self, closure: Closure) -> dict[str, Any]:
        raise NotImplementedError

    def home(self) -> str | None:
        """The target user's home directory when this side knows it (jobs run with a clean env)."""
        return None

    def enter(self, closure: Closure) -> list[str] | None:
        """Command prefix that runs a program inside this target's store view."""
        return None

    def _workdir(self, run_id: str) -> str:
        if not isinstance(run_id, str) or not _RUN.fullmatch(run_id):
            raise ValueError("run_id contains characters not allowed in a deployment path")
        return f"{self.run_root}/{run_id}"

    def _program(self, closure: Closure, name: str) -> str:
        if name == "run":
            return closure.program
        commands = closure.metadata.get("commands", {})
        if not isinstance(commands, Mapping) or name not in commands:
            raise KeyError(f"experiment does not declare program {name!r}")
        return commands[name]

    def launch(
        self,
        closure: Closure,
        *,
        run_id: str,
        argv: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        inputs: Mapping[str, Any] | None = None,
        program: str = "run",
    ) -> dict[str, Any]:
        self._admit(closure)
        self._verify(closure)
        workdir = self._workdir(run_id)
        records = _input_records(inputs)
        args, values = tuple(closure.metadata.get("argv", [])) + tuple(argv), dict(
            closure.metadata.get("env", {})
        )
        if env is not None:
            values.update(env)
        if any(
            not isinstance(k, str) or not k or "\x00" in k or not isinstance(v, str) or "\x00" in v
            for k, v in values.items()
        ):
            raise ValueError("environment names and values must be NUL-free strings")
        input_env, input_json = self._stage_inputs(workdir, records)
        values.update(input_env)
        values["NIX_DEPLOY_WORKDIR"] = workdir
        values.setdefault("PATH", HOST_PATH)
        home = self.home()
        if home:
            values.setdefault("HOME", home)
        enter = self.enter(closure)
        if enter:
            values["NIX_DEPLOY_ENTER"] = " ".join(shlex.quote(part) for part in enter)
        self._write_json(workdir, "inputs.json", input_json)
        handle = self._spawn(
            closure, workdir, run_id, self._program(closure, program), args, values
        )
        handle.update(
            {
                "id": run_id,
                "workdir": workdir,
                "closure": closure.as_dict(),
                "inputs": input_json,
                "program": program,
            }
        )
        self._write_json(workdir, "handle.json", handle)
        return handle

    def put(self, handle, name: str, source) -> str:
        """Copy bytes or a path into a running job's workdir (hash-verified); returns its target path."""
        ((n, data, digest),) = _input_records({name: source})
        _, manifest = self._stage_inputs(str(handle["workdir"]), [(n, data, digest)], existing=True)
        return manifest[0]["path"]

    def _stage_inputs(self, workdir: str, records, existing: bool = False):
        raise NotImplementedError

    def _spawn(self, closure, workdir, run_id, executable, argv, env):
        raise NotImplementedError

    def _write_json(self, workdir, name, value):
        raise NotImplementedError

    def read_events(self, handle, offset):
        return self._read_bytes(handle, "events.jsonl", offset, True)

    def read_file(self, handle, relpath):
        data = self._read_bytes(handle, _relative(relpath), None, True)
        return None if data is None else data.decode()

    def fetch(self, handle, relpath, dest):
        self._fetch(handle, _relative(relpath), Path(dest))
        return Path(dest)

    def exists(self, handle, relpath):
        return self._exists(handle, _relative(relpath))

    def _read_bytes(self, handle, relpath, offset, missing=False):
        raise NotImplementedError

    def _fetch(self, handle, relpath, target):
        raise NotImplementedError

    def _exists(self, handle, relpath):
        raise NotImplementedError

    def alive(self, handle):
        raise NotImplementedError

    def stop(self, handle):
        raise NotImplementedError

    def remove(self, handle):
        raise NotImplementedError

    def tunnel(self, handle, remote_port):
        raise NotImplementedError


__all__ = ["Backend", "normalize_manifest"]
