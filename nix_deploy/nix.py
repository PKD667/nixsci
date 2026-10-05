from __future__ import annotations
import json, subprocess
from pathlib import Path
from typing import Any


class NixError(RuntimeError):
    pass


def _command(nix, store, *args):
    return [
        nix,
        "--extra-experimental-features",
        "nix-command",
        *((["--store", store] if store else [])),
        *args,
    ]


def run(nix, store, *args, input=None):
    cmd = _command(nix, store, *args)
    result = subprocess.run(cmd, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise NixError(
            f"Nix command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.decode('utf-8','replace')[-3000:]}"
        )
    return result


def _json(result, what):
    raw = result.stdout.decode("utf-8", "replace").strip()
    try:
        return json.loads(raw)
    except ValueError as error:
        raise NixError(f"invalid JSON from Nix while reading {what}: {raw!r}") from error


def path_hashes(nix, store, root):
    entries = _json(run(nix, store, "path-info", "--recursive", "--json", root), "closure")
    if isinstance(entries, dict):
        entries = [{"path": path, **info} for path, info in entries.items()]
    if not isinstance(entries, list):
        raise NixError("Nix path-info did not return a list or path map")
    result = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("narHash"), str)
        ):
            raise NixError(f"Nix path-info entry lacks path/narHash: {entry!r}")
        if entry["path"] in result and result[entry["path"]] != entry["narHash"]:
            raise NixError(f"conflicting NAR hashes for {entry['path']}")
        result[entry["path"]] = entry["narHash"]
    if root not in result:
        raise NixError(f"Nix closure does not contain its root {root}")
    return result


def verify(nix, store, root):
    run(nix, store, "store", "verify", "--recursive", "--no-trust", root)
    return path_hashes(nix, store, root)


def capture_source(nix, flake):
    local_ref = flake[5:] if isinstance(flake, str) and flake.startswith("path:") else flake
    path = (
        Path(local_ref).expanduser()
        if isinstance(local_ref, str)
        and (local_ref.startswith(("/", "./", "../")) or Path(local_ref).exists())
        else None
    )
    if path is not None:
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(f"local flake is not a directory: {flake}")
        git = ["git", "-c", f"safe.directory={path}", "-C", str(path)]
        root = subprocess.run(
            [*git, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if root.returncode:
            raise RuntimeError(
                f"local flake {path} is not a Git checkout; refusing to copy an untracked/private source tree"
            )
        status = subprocess.run(
            [*git, "status", "--porcelain", "--untracked-files=all"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if status.returncode:
            raise RuntimeError(
                f"cannot inspect Git state for local flake {path}: {status.stderr.decode(errors='replace').strip()}"
            )
        if status.stdout:
            raise RuntimeError(
                f"local flake {path} is dirty or has untracked files; commit a clean tracked snapshot before resolve (source was not copied)"
            )
        head = subprocess.run(
            [*git, "rev-parse", "--verify", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if head.returncode:
            raise RuntimeError(
                f"local flake {path} has no Git commit; resolve requires a clean tracked snapshot (source was not copied)"
            )
        flake = f"git+file://{root.stdout.decode().strip()}?ref={head.stdout.decode().strip()}"
    archive = _json(
        run(nix, None, "flake", "archive", "--json", "--no-check-sigs", flake), "flake archive"
    )
    ref = archive.get("path") if isinstance(archive, dict) else None
    if not isinstance(ref, str) or not ref.startswith("/nix/store/"):
        raise NixError(f"flake archive did not return a store source: {archive!r}")
    metadata = _json(run(nix, None, "flake", "metadata", "--json", ref), "captured flake metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("locked"), dict):
        raise NixError("captured source lacks locked identity")
    return {"flake": flake, "snapshot": ref, "locked": metadata["locked"]}, ref


def build(nix, flake, installable, system):
    value = _json(
        run(
            nix,
            None,
            "build",
            "--no-link",
            "--no-update-lock-file",
            "--json",
            "--system",
            system,
            f"{flake}#{installable}",
        ),
        "build",
    )
    try:
        out = value[0]["outputs"]["out"]  # a path string since Nix 2.33, {"path": ...} before
        path = out["path"] if isinstance(out, dict) else out
    except (IndexError, KeyError, TypeError) as error:
        raise NixError(f"Nix build did not produce an out path: {value!r}") from error
    if not isinstance(path, str) or not path.startswith("/nix/store/"):
        raise NixError(f"invalid Nix output path: {path!r}")
    return path


def manifest(nix, root):
    try:
        value = json.loads(run(nix, None, "store", "cat", f"{root}/experiment.json").stdout)
    except ValueError as error:
        raise NixError("experiment.json is not valid JSON") from error
    if not isinstance(value, dict):
        raise NixError("experiment.json at the derivation root must be an object")
    return value


def source_identity(nix, flake):
    return capture_source(nix, flake)[0]


def copy_to(nix, destination, root):
    run(nix, None, "copy", "--no-check-sigs", "--to", destination, root)


def copy_to_cache(nix, cache, root):
    Path(cache).mkdir(parents=True, exist_ok=False)
    copy_to(nix, f"file://{cache}", root)


def command(nix, store, *args):
    return _command(nix, store, *args)


__all__ = [
    "NixError",
    "build",
    "capture_source",
    "command",
    "copy_to",
    "copy_to_cache",
    "manifest",
    "path_hashes",
    "run",
    "source_identity",
    "verify",
]
