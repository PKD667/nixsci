from __future__ import annotations
import json, re, subprocess, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class NixError(RuntimeError):
    pass


# The public binary cache and its signing key. A target fetches from here with signatures checked.
CACHE_URL = "https://cache.nixos.org"
CACHE_KEY = "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
_STORE_HASH = re.compile(r"[0-9a-df-np-sv-z]{32}")  # Nix base32: no e, o, t, u


class CacheUnreachable(RuntimeError):
    """The cache could not answer: a network or server error, not a missing path."""


def narinfo_exists(path: str, timeout: float = 10) -> bool:
    """True if CACHE_URL has a narinfo for this store path (a HEAD request, no download).

    A 404 is a miss (the path is project-specific). Any other failure raises CacheUnreachable, so a
    network outage is never read as an empty cache."""
    name = path.rsplit("/", 1)[-1]
    digest = name.split("-", 1)[0]
    if not _STORE_HASH.fullmatch(digest):
        return False
    request = urllib.request.Request(f"{CACHE_URL}/{digest}.narinfo", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise CacheUnreachable(f"HTTP {error.code} from {CACHE_URL}") from error
    except (urllib.error.URLError, OSError) as error:
        raise CacheUnreachable(f"{getattr(error, 'reason', error)}") from error


def cache_partition(paths, has=narinfo_exists):
    """Split `paths` (closure order kept) into (on the cache, not on the cache, reason).

    If the cache cannot be asked, every path is 'not on the cache' and `reason` says why: the caller
    then exports everything from the controller and reports that one reason."""
    paths = list(paths)
    try:
        # One HEAD per path: overlapped, since each is a round trip and a closure has dozens of paths.
        with ThreadPoolExecutor(max_workers=16) as pool:
            flags = [bool(f) for f in pool.map(has, paths)]
    except CacheUnreachable as error:
        return [], paths, f"controller cannot reach {CACHE_URL}: {error}"
    return ([p for p, f in zip(paths, flags) if f],
            [p for p, f in zip(paths, flags) if not f],
            None)


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


def _dirty_paths(porcelain: bytes) -> list[str]:
    """Paths from `git status --porcelain` output (renames count as their new path)."""
    paths = []
    for line in porcelain.decode(errors="replace").splitlines():
        if len(line) > 3:
            paths.append(line[3:].split(" -> ")[-1].strip().strip('"'))
    return paths


def capture_source(nix, flake, ignore=()):
    """Archive the flake into the store and return its identity.

    A local flake must be a clean git checkout: the source is the committed state. `ignore` lists
    glob patterns (matched against the path or its basename) for files whose changes do not count
    as dirty, for output files of a tool that live in the repository (a lock file, say).
    """
    import fnmatch

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
        dirty = [
            p
            for p in _dirty_paths(status.stdout)
            if not any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(Path(p).name, g) for g in ignore)
        ]
        if dirty:
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
    # `locked` above describes the store snapshot (a path: source), which cannot be fetched on
    # another machine. `origin` is the locked identity of the flake as given (rev, narHash, url),
    # the part a later reader can rebuild the source from.
    original = _json(run(nix, None, "flake", "metadata", "--json", flake), "flake metadata")
    origin = original.get("locked") if isinstance(original, dict) else None
    return {
        "flake": flake,
        "snapshot": ref,
        "locked": metadata["locked"],
        "origin": origin,
    }, ref


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
    """Read deployment format, retaining the old experiment manifest as a warned fallback."""
    try:
        raw = run(nix, None, "store", "cat", f"{root}/deployment.json").stdout
    except NixError:
        import warnings
        warnings.warn("deployment closure has no deployment.json; reading legacy experiment.json", RuntimeWarning)
        try:
            raw = run(nix, None, "store", "cat", f"{root}/experiment.json").stdout
        except NixError as error:
            raise NixError("closure has neither deployment.json nor experiment.json") from error
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise NixError("deployment.json is not valid JSON") from error
    if not isinstance(value, dict):
        raise NixError("deployment.json at the derivation root must be an object")
    return value


def source_identity(nix, flake):
    return capture_source(nix, flake)[0]


def copy_to(nix, destination, root):
    run(nix, None, "copy", "--no-check-sigs", "--to", destination, root)


def copy_to_cache(nix, cache, *paths, recursive=True):
    if not paths:
        raise ValueError("cache export requires at least one store path")
    Path(cache).mkdir(parents=True, exist_ok=False)
    # GPU wheels contain large compressed binaries: xz spends disproportionate
    # controller time here. Encoding is independent of verified NAR identity.
    run(nix, None, "copy", "--no-check-sigs",
        *([] if recursive else ["--no-recursive"]),
        "--to", f"file://{cache}?compression=zstd", *paths)


def export_paths(nix, destination, *paths):
    """Native export accepts a partial graph; unlike a binary cache, its
    references need not be present in the transport directory. Import checks
    those references against the destination store before registration.
    """
    if not paths:
        raise ValueError("native export requires at least one store path")
    with Path(destination).open("xb") as stream:
        result = subprocess.run(["nix-store", "--export", *paths], executable=nix,
                                stdout=stream, stderr=subprocess.PIPE)
    if result.returncode:
        raise NixError(f"Nix native export failed ({result.returncode}): "
                       + result.stderr.decode("utf-8", "replace")[-3000:])


def command(nix, store, *args):
    return _command(nix, store, *args)


__all__ = [
    "NixError",
    "build",
    "capture_source",
    "command",
    "copy_to",
    "copy_to_cache",
    "export_paths",
    "manifest",
    "path_hashes",
    "run",
    "source_identity",
    "verify",
]
