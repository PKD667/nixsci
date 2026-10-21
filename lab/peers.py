"""Pull locked runs from any peer, push to peers, or do both. No distinguished server.

Peers are Nix store URLs (ssh-ng://host, file:///cache, s3://bucket, ...). Configure locally:
~/.config/nixsci/peers.json = {"peers": ["ssh-ng://a", "ssh-ng://b"]}.
/etc/nixsci/peers.json supplies defaults; NIXSCI_PEERS_FILE overrides both.

Every pull verifies the lock's content hash and roots the run against Nix garbage collection.
Push roots on ssh/ssh-ng destinations too (requires an ssh login with nix-store). Binary caches
(file://, S3, ...) keep their objects without roots. Pulling on each machine is enough to share
runs in both directions; the lock in git is the inventory, not a remote mutable database.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def peers() -> list[str]:
    override = os.environ.get("NIXSCI_PEERS_FILE")
    paths = [Path(override)] if override else [Path("/etc/nixsci/peers.json"),
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "nixsci/peers.json"]
    result = []
    for path in paths:
        if path.exists():
            values = json.loads(path.read_text())["peers"]
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"{path}: peers must be a list of Nix store URLs")
            result.extend(values)
    return list(dict.fromkeys(result))


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def root(path: str) -> None:
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "nixsci/roots"
    state.mkdir(parents=True, exist_ok=True)
    link = state / Path(path).name
    if link.is_symlink() and os.readlink(link) == path:
        return
    p = _run("nix-store", "--realise", path, "--add-root", str(link), "--indirect")
    if p.returncode:
        raise RuntimeError(f"cannot root {path}: {p.stderr.strip()}")


def remote_root(remote: str, path: str) -> None:
    url = urlparse(remote)
    if url.scheme not in ("ssh", "ssh-ng"):
        return  # a binary cache is not garbage collected as a store
    if not url.hostname:
        raise ValueError(f"invalid ssh store URL: {remote}")
    host = (url.username + "@" if url.username else "") + url.hostname
    # Relative to the remote home; no private input, key, or machine name enters the run hash.
    link = ".local/state/nixsci/roots/" + Path(path).name
    script = ("mkdir -p .local/state/nixsci/roots && "
              "nix-store --realise " + shlex.quote(path) + " --add-root " + shlex.quote(link) + " --indirect")
    cmd = ["ssh"] + (["-p", str(url.port)] if url.port else []) + [host, script]
    p = _run(*cmd)
    if p.returncode:
        raise RuntimeError(f"copied to {remote}, but could not root {path}: {p.stderr.strip()}")


def canonical_path(entry: dict) -> str:
    p = _run("nix-store", "--print-fixed-path", "--recursive", "sha256", entry["sha256"], entry["name"])
    if p.returncode:
        raise ValueError(f"invalid run identity {entry['name']!r}: {p.stderr.strip()}")
    path = p.stdout.strip()
    if entry.get("path", path) != path:
        raise ValueError(f"{entry['name']}: lock path differs from the path derived from name and hash")
    return path


def verify(entry: dict) -> None:
    p = _run("nix", "--extra-experimental-features", "nix-command", "path-info", "--json", entry["path"])
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    data = json.loads(p.stdout)
    info = data[entry["path"]] if isinstance(data, dict) else data[0]
    if info["narHash"] != entry["sha256"]:
        raise ValueError(f"{entry['name']}: store content hash differs from lab.lock")


def transfer(project: Path, action: str, remote: str | None = None, log=print) -> int:
    from .publish import read_lock

    remotes = [remote] if remote else peers()
    if not remotes:
        raise ValueError("no peers: pass a Nix store URL or write ~/.config/nixsci/peers.json")
    entries = [e for runs in read_lock(project).values() for e in runs]
    if not entries:
        raise ValueError(f"{project}/lab.lock lists no runs")
    failed = False
    for e in entries:
        path = canonical_path(e)
        if action in ("pull", "sync"):
            if not Path(path).exists():
                errors = []
                for source in remotes:
                    p = _run("nix", "--extra-experimental-features", "nix-command", "copy", "--from", source, path)
                    if p.returncode == 0:
                        log(f"{e['name']}: pulled from {source}")
                        break
                    errors.append(f"{source}: {p.stderr.strip()}")
                else:
                    log(f"{e['name']}: not found at any peer\n" + "\n".join(errors))
                    failed = True
                    continue
            verify(e)
            root(path)
        if action in ("push", "sync"):
            verify(e)
            root(path)
            for dest in remotes:
                p = _run("nix", "--extra-experimental-features", "nix-command", "copy", "--to", dest, path)
                if p.returncode:
                    log(f"{e['name']}: cannot push to {dest}: {p.stderr.strip()}")
                    failed = True
                else:
                    remote_root(dest, path)
                    log(f"{e['name']}: pushed to {dest}")
    return int(failed)
