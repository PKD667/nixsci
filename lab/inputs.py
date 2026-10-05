"""Datasets and models: content-addressed inputs, shared by every project that reaches a store.

A store is a directory, usually on the shared storage of a grid:

    blobs/<sha256>            the bytes: a Parquet table, a model file
    manifests/<sha256>.json   what a revision is: kind, name, parent, number, blobs, details
    names/<name>/<sha256>     one empty file per revision of a name

Everything is written once and named by its hash, so any number of writers can share one store
without locks: a rename is atomic, and two writers of the same bytes write the same name. A
revision's number is its depth in the chain of parents, so no allocator hands out numbers. Two
revisions that extend the same parent are a fork: both carry the same number, and a reference that
cannot tell them apart is an error that lists them.

A dataset is one Parquet table. Its columns, types, row count and units are read from the file.
A model is one file with an optional declaration of its inputs and outputs. What an import could
not learn is recorded under `gaps`, never guessed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

KINDS = ("dataset", "model")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_REF = re.compile(r"^(?:(dataset|model):)?([A-Za-z][A-Za-z0-9_.-]*)(?:@([0-9]+)|#([0-9a-f]{4,64}))?$")
_CHUNK = 1 << 20


class Missing(LookupError):
    pass


class Ambiguous(LookupError):
    pass


def canonical(body: Any) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def blob_path(self, sha256: str) -> Path:
        return self.root / "blobs" / sha256

    def put_blob(self, source: str | Path) -> dict[str, Any]:
        """Copy a file into the store, hashing it on the way. Returns its blob entry."""
        source = Path(source)
        directory = self.root / "blobs"
        directory.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as out, source.open("rb") as handle:
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
            sha256 = digest.hexdigest()
            final = directory / sha256
            if final.exists():
                Path(tmp).unlink()
            else:
                os.chmod(tmp, 0o644)
                os.replace(tmp, final)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return {"name": source.name, "sha256": sha256, "bytes": size}

    def manifest(self, sha256: str) -> dict[str, Any]:
        path = self.root / "manifests" / f"{sha256}.json"
        if not path.is_file():
            raise Missing(f"no revision {sha256[:12]} in {self.root}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"{path} does not match its name: it was modified")
        return json.loads(data)

    def revisions(self, name: str) -> dict[str, dict[str, Any]]:
        directory = self.root / "names" / name
        if not directory.is_dir():
            return {}
        return {p.name: self.manifest(p.name) for p in sorted(directory.iterdir()) if not p.name.startswith(".")}

    def tips(self, name: str) -> dict[str, dict[str, Any]]:
        """The revisions no other revision extends: one, unless the name has forked."""
        revisions = self.revisions(name)
        parents = {body["parent"] for body in revisions.values()}
        return {sha: body for sha, body in revisions.items() if sha not in parents}

    def names(self) -> list[str]:
        directory = self.root / "names"
        return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []

    def add(
        self,
        kind: str,
        name: str,
        blobs: Sequence[Mapping[str, Any]],
        details: Mapping[str, Any] | None = None,
        *,
        source: Sequence[str] = (),
        gaps: Sequence[str] = (),
        made_by: str | None = None,
        parent: str | None = None,
    ) -> tuple[str, bool]:
        """Record a revision of `name` -> (its hash, whether it is new).

        Without `parent` the new revision extends the tip of the name. Adding what the parent
        already holds changes nothing and returns the parent.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
        if not _NAME.fullmatch(name):
            raise ValueError(f"name must match {_NAME.pattern}, not {name!r}")
        existing = self.revisions(name)
        for body in existing.values():
            if body["kind"] != kind:
                raise ValueError(f"{name!r} is a {body['kind']}, not a {kind}")
        if parent is None and existing:
            tips = self.tips(name)
            if len(tips) != 1:
                raise Ambiguous(f"{name!r} has {len(tips)} tips; choose the parent with parent=<hash>")
            parent = next(iter(tips))
        elif parent is not None and parent not in existing:
            raise Missing(f"{name!r} has no revision {parent[:12]}")
        body = {
            "kind": kind,
            "name": name,
            "parent": parent,
            "number": existing[parent]["number"] + 1 if parent else 1,
            "blobs": sorted((dict(b) for b in blobs), key=lambda b: b["name"]),
            "details": dict(details or {}),
            "source": list(source),
            "gaps": list(gaps),
            "made_by": made_by,
        }
        if parent is not None:
            before = existing[parent]
            if all(body[key] == before[key] for key in body if key not in ("parent", "number")):
                return parent, False
        data = canonical(body)
        sha256 = hashlib.sha256(data).hexdigest()
        _write_once(self.root / "manifests" / f"{sha256}.json", data)
        _write_once(self.root / "names" / name / sha256, b"")
        return sha256, True

    def resolve(self, ref: str) -> str:
        """`name`, `name@3` or `name#9c1f`, each with an optional `dataset:` or `model:` -> a hash."""
        match = _REF.match(ref)
        if not match:
            raise ValueError(f"bad reference {ref!r}: write name, name@3 or name#<hash prefix>")
        kind, name, number, prefix = match.groups()
        revisions = self.revisions(name)
        if not revisions:
            raise Missing(f"no dataset or model named {name!r} in {self.root}")
        if kind and any(body["kind"] != kind for body in revisions.values()):
            raise Missing(f"{name!r} is not a {kind}")
        if number is not None:
            found = {s: b for s, b in revisions.items() if b["number"] == int(number)}
        elif prefix is not None:
            found = {s: b for s, b in revisions.items() if s.startswith(prefix)}
        else:
            found = self.tips(name)
        if not found:
            raise Missing(f"{ref!r} matches no revision of {name!r}")
        if len(found) > 1:
            listing = ", ".join(f"{s[:12]} (number {b['number']})" for s, b in sorted(found.items()))
            raise Ambiguous(f"{ref!r} matches {len(found)} revisions: {listing}; pin one with {name}#<hash>")
        return next(iter(found))

    def verify(self, sha256: str) -> list[tuple[str, str]]:
        """Re-hash every blob of a revision -> [(file, 'missing' | 'changed')]."""
        problems = []
        for blob in self.manifest(sha256)["blobs"]:
            path = self.blob_path(blob["sha256"])
            if not path.is_file():
                problems.append((blob["name"], "missing"))
            elif _sha256_file(path) != blob["sha256"]:
                problems.append((blob["name"], "changed"))
        return problems

    def diff(self, a: str, b: str) -> dict[str, Any]:
        """What changed between two revisions, from their manifests alone."""
        left, right = self.manifest(a), self.manifest(b)
        before, after = left["details"].get("columns", {}), right["details"].get("columns", {})
        size = lambda m: sum(x["bytes"] for x in m["blobs"])  # noqa: E731
        return {
            "added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "retyped": {c: [before[c], after[c]] for c in sorted(before.keys() & after.keys()) if before[c] != after[c]},
            "rows": [left["details"].get("rows"), right["details"].get("rows")],
            "bytes": [size(left), size(right)],
        }

    def import_dataset(
        self, name: str, file: str | Path, *, units: Mapping[str, str] | None = None,
        source: Sequence[str] = (), parent: str | None = None,
    ) -> tuple[str, bool]:
        details, gaps = describe_parquet(file, units)
        return self.add("dataset", name, [self.put_blob(file)], details, source=source, gaps=gaps, parent=parent)

    def import_model(
        self, name: str, file: str | Path, *, io: Mapping[str, Any] | None = None,
        source: Sequence[str] = (), parent: str | None = None,
    ) -> tuple[str, bool]:
        gaps = [] if io else ["input and output specification"]
        return self.add("model", name, [self.put_blob(file)], {"io": dict(io)} if io else {},
                        source=source, gaps=gaps, parent=parent)


def describe_parquet(path: str | Path, units: Mapping[str, str] | None = None) -> tuple[dict[str, Any], list[str]]:
    """Columns, row count and units of a Parquet file, and the units it could not learn."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise SystemExit("importing a dataset needs pyarrow: install nixsci[arrow]") from error
    schema = pq.read_schema(path)
    units = dict(units or {})
    unknown = sorted(set(units) - set(schema.names))
    if unknown:
        raise ValueError(f"units name columns the table does not have: {unknown}")
    numeric = [f.name for f in schema if pa.types.is_integer(f.type) or pa.types.is_floating(f.type)]
    return (
        {"columns": {f.name: str(f.type) for f in schema}, "rows": pq.read_metadata(path).num_rows, "units": units},
        [f"unit of {c}" for c in numeric if c not in units],
    )
