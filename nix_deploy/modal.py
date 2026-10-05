"""Modal provider for controller-built immutable Nix OCI closures.

The controller publishes the closure as an OCI image before this provider is
constructed.  Modal receives only the immutable registry reference and an
explicit run payload; the deployed generic function is responsible for
executing ``program`` in the mounted run volume.  No project code is imported
here and no image is built or pulled by this module during configuration.

The configured function has this payload contract::

    {"program": "/nix/store/.../run", "argv": [...], "env": {...},
     "workdir": "/run/nix-deploy/<run-id>", "volume": "..."}

It writes ``events.jsonl`` and artifacts below ``workdir`` in the same Modal
Volume.  The function is pre-deployed from the same immutable image digest;
this provider deliberately does not invent or deploy a second app.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

_RUN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class Modal:
    """Use a pre-deployed generic Modal function for one immutable image.

    ``config`` is intentionally explicit.  Required keys are ``registry``
    (an OCI repository), ``image_digest``, ``app``, ``function``, ``volume``,
    ``run_root`` and ``gpu``.  ``scaledown_window``, ``min_containers``,
    ``enable_memory_snapshot`` and ``enable_gpu_snapshot`` describe the
    already-deployed function; scale-to-zero and both snapshots are required
    and are recorded in handles so a run can be audited without querying
    mutable provider state.
    """

    backend_name = "modal"

    def __init__(self, config: dict):
        if not isinstance(config, dict):
            raise TypeError("Modal config must be an object")
        required = ("registry", "image_digest", "app", "function", "volume", "run_root", "gpu")
        missing = [
            key for key in required if not isinstance(config.get(key), str) or not config[key]
        ]
        if missing:
            raise RuntimeError("Modal setup is incomplete; missing " + ", ".join(missing))
        if not _DIGEST.fullmatch(config["image_digest"]):
            raise RuntimeError("Modal setup requires an immutable image_digest=sha256:<64 hex>")
        registry = config["registry"].rstrip("/")
        if "@" in registry or ":" in registry.rsplit("/", 1)[-1]:
            raise RuntimeError("Modal registry must be an OCI repository without a tag or digest")
        if not config["run_root"].startswith("/"):
            raise ValueError("Modal run_root must be an absolute volume path")
        if config.get("min_containers", 0) != 0:
            raise RuntimeError("Modal deployment must keep min_containers=0 for scale-to-zero")
        if (
            config.get("enable_memory_snapshot") is not True
            or config.get("enable_gpu_snapshot") is not True
        ):
            raise RuntimeError("Modal deployment must enable memory and GPU snapshots")
        if not isinstance(config.get("scaledown_window"), int) or config["scaledown_window"] <= 0:
            raise RuntimeError("Modal deployment requires a positive scaledown_window")
        try:
            import modal  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Modal provider requires the pinned modal SDK 1.5.2") from exc
        if getattr(modal, "__version__", None) != "1.5.2":
            raise RuntimeError(
                f"Modal provider requires SDK 1.5.2, found {getattr(modal, '__version__', 'unknown')}"
            )
        self.modal = modal
        self.config = dict(config)
        self.image_ref = f"{registry}@{config['image_digest']}"
        self.image = modal.Image.from_registry(self.image_ref)
        self.app_name = config["app"]
        self.function_name = config["function"]
        self.volume_name = config["volume"]
        self.run_root = config["run_root"].rstrip("/")
        self.volume = modal.Volume.from_name(
            self.volume_name,
            create_if_missing=bool(config.get("create_volume", False)),
        )
        self.function = modal.Function.from_name(self.app_name, self.function_name)

    def stage(self, closure) -> dict[str, Any]:
        """Validate the closure-to-image binding; publishing is controller-owned."""
        manifest = closure.as_dict() if hasattr(closure, "as_dict") else dict(closure)
        metadata = manifest.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("closure metadata must be an object")
        declared = metadata.get("image") or metadata.get("oci_image")
        if declared is not None and declared != self.image_ref:
            raise RuntimeError(
                f"closure image {declared!r} does not match configured Modal image {self.image_ref!r}"
            )
        return {
            "image": self.image_ref,
            "system": manifest.get("system"),
            "closure": manifest.get("closure", {}),
        }

    def _workdir(self, run_id: str) -> str:
        if not isinstance(run_id, str) or not _RUN.fullmatch(run_id):
            raise ValueError("run_id contains characters not allowed in a deployment path")
        return f"{self.run_root}/{run_id}"

    @staticmethod
    def _records(inputs: Mapping[str, Any] | None) -> list[tuple[str, str | bytes, str]]:
        if inputs is None:
            return []
        if not isinstance(inputs, Mapping):
            raise TypeError("inputs must be an explicit name-to-path mapping")
        out = []
        for name, value in inputs.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError(f"invalid input name {name!r}")
            expected = None
            if isinstance(value, Mapping):
                expected = value.get("sha256")
                value = value.get("path", value.get("data"))
            if isinstance(value, (str, os.PathLike)):
                source = Path(value)
                if not source.exists():
                    raise FileNotFoundError(source)
                digest = hashlib.sha256()
                if source.is_file():
                    digest.update(source.read_bytes())
                else:
                    for child in sorted(source.rglob("*")):
                        if child.is_file():
                            digest.update(child.relative_to(source).as_posix().encode() + b"\0")
                            digest.update(child.read_bytes())
                actual = digest.hexdigest()
                source_value: str | bytes = str(source)
            elif isinstance(value, bytes):
                actual = hashlib.sha256(value).hexdigest()
                source_value = value
            else:
                raise TypeError(f"input {name!r} must be a path, bytes, or mapping")
            if expected is not None and expected != actual:
                raise ValueError(f"private input {name!r} hash mismatch")
            out.append((name, source_value, actual))
        return out

    async def _upload_one(self, remote: str, source: str | bytes) -> None:
        async with self.volume.batch_upload(force=False) as batch:
            if isinstance(source, bytes):
                batch.put_file(io.BytesIO(source), remote)
            elif Path(source).is_dir():
                batch.put_directory(source, remote)
            else:
                batch.put_file(source, remote)

    def _run_async(self, awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        raise RuntimeError("Modal lifecycle calls must not run inside an active asyncio loop")

    def launch(
        self,
        closure,
        *,
        run_id: str,
        argv=(),
        env: Mapping[str, str] | None = None,
        inputs: Mapping[str, Any] | None = None,
        program: str = "run",
    ) -> dict[str, Any]:
        manifest = closure.as_dict() if hasattr(closure, "as_dict") else dict(closure)
        self.stage(closure)
        metadata = manifest.get("metadata", {})
        if program != "run":
            commands = metadata.get("commands", {})
            if not isinstance(commands, Mapping) or program not in commands:
                raise KeyError(f"experiment does not declare program {program!r}")
            executable = commands[program]
        else:
            executable = manifest["program"]
        if not isinstance(executable, str) or not executable.startswith("/nix/store/"):
            raise ValueError("closure program must be an immutable store executable")
        callbacks = metadata.get("callbacks", {})
        if not isinstance(callbacks, Mapping):
            raise ValueError("closure callbacks must be an object")
        checked_callbacks = {}
        for name, callback in callbacks.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError(f"invalid callback name {name!r}")
            if not isinstance(callback, str) or not callback.startswith("/nix/store/"):
                raise ValueError(f"callback {name!r} must be an immutable store executable")
            checked_callbacks[name] = callback
        workdir = self._workdir(run_id)
        records = self._records(inputs)
        remote_inputs = []
        for name, source, digest in records:
            remote = f"{workdir}/inputs/{name}"
            self._run_async(self._upload_one(remote, source))
            remote_inputs.append({"name": name, "path": remote, "sha256": digest})
        values = dict(metadata.get("env", {}))
        if env is not None:
            values.update(dict(env))
        values.update({f"NIX_DEPLOY_INPUT_{item['name']}": item["path"] for item in remote_inputs})
        values["NIX_DEPLOY_WORKDIR"] = workdir
        payload = {
            "program": executable,
            "argv": list(metadata.get("argv", [])) + list(argv),
            "env": values,
            "workdir": workdir,
            "volume": self.volume_name,
            "callbacks": checked_callbacks,
            "modal": {
                "gpu": self.config.get("gpu"),
                "scaledown_window": self.config.get("scaledown_window", 300),
                "min_containers": self.config.get("min_containers", 0),
                "enable_memory_snapshot": self.config.get("enable_memory_snapshot", True),
                "enable_gpu_snapshot": self.config.get("enable_gpu_snapshot", True),
            },
        }
        call = self.function.spawn(payload)
        handle = {
            "id": run_id,
            "call_id": call.object_id,
            "workdir": workdir,
            "volume": self.volume_name,
            "image": self.image_ref,
            "program": program,
            "inputs": remote_inputs,
            "closure": manifest,
        }
        self._run_async(
            self._upload_one(f"{workdir}/handle.json", json.dumps(handle, sort_keys=True).encode())
        )
        return handle

    async def _read(self, path: str) -> bytes:
        chunks = []
        async for chunk in self.volume.read_file(path):
            chunks.append(chunk)
        return b"".join(chunks)

    def read_events(self, handle: Mapping[str, Any], offset: int) -> bytes:
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be non-negative")
        try:
            return self._run_async(self._read(f"{handle['workdir']}/events.jsonl"))[offset:]
        except FileNotFoundError:
            return b""

    def read_file(self, handle: Mapping[str, Any], relpath: str) -> str | None:
        path = self._relative(relpath)
        try:
            return self._run_async(self._read(f"{handle['workdir']}/{path}")).decode()
        except FileNotFoundError:
            return None

    def exists(self, handle: Mapping[str, Any], relpath: str) -> bool:
        path = self._relative(relpath)
        try:
            entries = self._run_async(
                self.volume.listdir(f"{handle['workdir']}/{path}", recursive=False)
            )
        except FileNotFoundError:
            return False
        return bool(entries)

    def fetch(self, handle: Mapping[str, Any], relpath: str, dest: str | Path) -> Path:
        path = self._relative(relpath)
        remote = f"{handle['workdir']}/{path}"
        entries = self._run_async(self.volume.listdir(remote, recursive=True))
        target = Path(dest)
        if len(entries) == 1 and getattr(entries[0], "path", "") == remote:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self._run_async(self._read(remote)))
            return target
        target.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            entry_path = getattr(entry, "path", "")
            kind = getattr(getattr(entry, "type", None), "name", "file").lower()
            if not entry_path or kind != "file":
                continue
            relative = PurePosixPath(entry_path).relative_to(PurePosixPath(remote))
            output = target / Path(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(self._run_async(self._read(entry_path)))
        return target

    def alive(self, handle: Mapping[str, Any]) -> bool:
        try:
            call = self.modal.FunctionCall.from_id(handle["call_id"])
            call.get(timeout=0)
        except TimeoutError:
            return True
        except Exception as exc:
            if type(exc).__name__ in {"TimeoutError", "OutputAlreadySaved"}:
                return True
            return False
        return False

    def stop(self, handle: Mapping[str, Any]) -> None:
        call = self.modal.FunctionCall.from_id(handle["call_id"])
        call.cancel(terminate_containers=True)

    def remove(self, handle: Mapping[str, Any]) -> None:
        self._run_async(self.volume.remove_file(handle["workdir"], recursive=True))

    def tunnel(self, handle: Mapping[str, Any], remote_port: int):
        if not isinstance(remote_port, int) or not 1 <= remote_port <= 65535:
            raise ValueError("remote_port must be a TCP port")
        url = handle.get("url") or self.config.get("url")
        if not isinstance(url, str) or not url:
            raise RuntimeError(
                "Modal function has no declared web endpoint; set config.url for inference"
            )
        return url, lambda: None

    @staticmethod
    def _relative(path: str) -> str:
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in PurePosixPath(path).parts
        ):
            raise ValueError("path must be relative to the Modal run volume")
        return path


__all__ = ["Modal"]
