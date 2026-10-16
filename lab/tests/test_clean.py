"""Runs name a commit, the lock holds the runs of the current code, a crash leaves nothing behind."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from nixsci import lab

SCHEMA = {"loss": {"epoch": "int", "value": "float"}}


def _run(root: Path, name: str, seed: int, closure: str | None = None, params: dict | None = None) -> Path:
    run = root / name
    (run / "lab").mkdir(parents=True)
    os.environ["NIX_LAB_DIR"] = str(run / "lab")
    os.environ["NIX_LAB_SCHEMA"] = json.dumps(SCHEMA)
    try:
        lab.record("loss", {"epoch": 1, "value": float(seed)})
    finally:
        for k in ("NIX_LAB_DIR", "NIX_LAB_SCHEMA"):
            os.environ.pop(k)
    manifest = {"app": "e", "run": name, "state": "ok", "seed": seed, "params": params or {}, "schema": SCHEMA}
    if closure is not None:
        manifest["closure"] = closure
    (run / "manifest.json").write_text(json.dumps(manifest))
    return run


class Clean(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_one_compaction_writes_every_run_and_their_rows_in_order(self):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nixsci.lab.compact import compact_runs

        runs = [_run(self.root, f"e-{i}", i) for i in (1, 0)]
        compact_runs(self.root / "out", runs)
        rows = json.loads((self.root / "out" / "runs.json").read_text())
        self.assertEqual([r["run"] for r in rows], ["e-0", "e-1"])
        self.assertFalse((self.root / "out" / "row.json").exists())
        values = {f.stem: pq.read_table(f).to_pylist()[0]["value"] for f in (self.root / "out" / "loss").iterdir()}
        self.assertEqual(values, {"e-0": 0.0, "e-1": 1.0})

    def test_the_lock_keeps_runs_of_the_current_code_and_spec_and_runs_recorded_by_hand(self):
        from nixsci.lab.publish import read_lock, retain

        now = "/nix/store/bbb-experiment"
        runs = {
            "old": _run(self.root, "old", 0, "/nix/store/aaa-experiment", {"x": 0.1}),
            "new": _run(self.root, "new", 0, now, {"x": 0.1}),
            "unswept": _run(self.root, "unswept", 0, now, {"x": 0.3}),
            "reseeded": _run(self.root, "reseeded", 7, now, {"x": 0.1}),
            "hand": _run(self.root, "hand", 0),
        }
        lock = {"e": [{"name": n, "path": str(p), "sha256": "x"} for n, p in runs.items()],
                "other": [{"name": "o", "path": "/does/not/matter", "sha256": "x"}]}
        (self.root / "lab.lock").write_text(json.dumps(lock))
        self.assertEqual(retain(self.root, "e", {now}, [(0, {"x": 0.1})]), ["old", "unswept", "reseeded"])
        after = read_lock(self.root)
        self.assertEqual([e["name"] for e in after["e"]], ["new", "hand"])
        self.assertEqual(after["other"], lock["other"])

    def test_uncommitted_code_is_refused_before_anything_runs(self):
        from nixsci.lab import cli

        spec = self.root / "spec.json"
        spec.write_text(json.dumps({"kind": "experiment", "name": "e"}))
        (self.root / ".git").mkdir()
        for source in ({}, {"rev": None}, {"rev": "abc123-dirty"}):
            argv = ["--project", str(self.root), "exec", "--spec", str(spec),
                    "--closure", "x86_64-linux=/nonexistent", "--source", json.dumps(source)]
            self.assertEqual(cli.main(argv), 2, source)
        self.assertFalse((self.root / "lab.lock").exists())


class Interrupted(unittest.TestCase):
    def setUp(self):
        from nixsci.deploy.native import Native

        self.runs = Path(tempfile.mkdtemp())
        self.backend = Native(system="x86_64-linux", run_root=str(self.runs))

    def test_a_dead_attempt_is_cleared_and_a_live_one_is_not(self):
        (self.runs / "r1" / "inputs").mkdir(parents=True)
        self.assertTrue(self.backend.clear("r1"))
        self.assertFalse((self.runs / "r1").exists())
        self.assertFalse(self.backend.clear("r1"))

        pgid, start = self.backend._identity(os.getpid())
        (self.runs / "r2").mkdir()
        (self.runs / "r2" / "handle.json").write_text(
            json.dumps({"pid": os.getpid(), "pgid": pgid, "start_time": start, "workdir": str(self.runs / "r2")})
        )
        with self.assertRaises(RuntimeError):
            self.backend.clear("r2")
        self.assertTrue((self.runs / "r2").exists())


if __name__ == "__main__":
    unittest.main()
