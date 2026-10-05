import json
import os
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

import lab
from lab import home
from lab.run import sha256_file
from nix_lab import lock, spec as spec_mod, sync

SPEC = textwrap.dedent("""
    [experiment]
    name = "meas"
    [data.size]
    columns = { n = "int", seconds = "float" }
    key = ["n"]
    [pipeline.curve]
    script = "curve.R"
    """)


class Env(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.old = {k: os.environ.get(k) for k in ("NIX_LAB_STORE", "XDG_DATA_HOME")}
        os.environ["NIX_LAB_STORE"] = str(self.store)
        self.addCleanup(self.restore)
        self.spec_path = self.root / "proj" / "meas.toml"
        self.spec_path.parent.mkdir()
        self.spec_path.write_text(SPEC)
        (self.spec_path.parent / "curve.R").write_text("# script\n")

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def make_analysed_store(self, store=None):
        """A run in the store plus a successful pipeline output, as `build` would leave it."""
        with lab.Run(None, "meas-r1", spec=self.spec_path, seed=1) as run:
            run.record("size", {"n": 1, "seconds": 2.0})
        runs = home.runs_dir()
        manifest = runs / "meas" / "meas-r1" / "manifest.json"
        out = home.analysis_dir() / "meas" / "curve"
        out.mkdir(parents=True)
        (out / "table.csv").write_text("n,seconds\n1,2.0\n")
        (out / "provenance.json").write_text(
            json.dumps(
                {
                    "exit_code": 0,
                    "fingerprint": "f" * 8,
                    "script_sha256": "s" * 8,
                    "inputs": [
                        {"app": "meas", "run": "meas-r1", "manifest_sha256": sha256_file(manifest)}
                    ],
                }
            )
        )


class Location(Env):
    def test_the_store_comes_from_the_environment_then_xdg(self):
        self.assertEqual(home.store_root(), self.store)
        os.environ.pop("NIX_LAB_STORE")
        os.environ["XDG_DATA_HOME"] = str(self.root / "xdg")
        self.assertEqual(home.store_root(), self.root / "xdg" / "nix-lab")

    def test_a_run_without_a_root_lands_in_the_store(self):
        with lab.Run(None, "r", spec=self.spec_path) as run:
            run.record("size", {"n": 1, "seconds": 1.0})
        self.assertTrue((self.store / "runs" / "meas" / "r" / "manifest.json").is_file())


class Lock(Env):
    def lock_data(self):
        spec = spec_mod.load(self.spec_path)
        return lock.collect(spec, home.runs_dir(), home.analysis_dir() / "meas")

    def test_collect_write_read_roundtrip(self):
        self.make_analysed_store()
        data = self.lock_data()
        self.assertEqual([r["run"] for r in data["run"]], ["meas-r1"])
        self.assertEqual(data["pipeline"][0]["runs"], ["meas-r1"])
        self.assertEqual(list(data["pipeline"][0]["outputs"]), ["table.csv"])
        written = lock.write(self.spec_path, data)
        self.assertEqual(written.name, "meas.lab.lock")
        parsed = tomllib.loads(written.read_text())
        self.assertEqual(parsed["run"][0]["records_sha256"], data["run"][0]["records_sha256"])
        self.assertEqual(lock.read(self.spec_path)["app"], "meas")

    def test_check_distinguishes_missing_from_changed(self):
        self.make_analysed_store()
        data = self.lock_data()
        runs, analysis = home.runs_dir(), home.analysis_dir() / "meas"
        self.assertEqual(lock.check(data, runs, analysis), [])
        with (runs / "meas" / "meas-r1" / "records.jsonl").open("a") as f:
            f.write("\n")
        (analysis / "curve" / "table.csv").unlink()
        kinds = {(k, w.split("/")[0]) for k, w, _ in lock.check(data, runs, analysis)}
        self.assertEqual(kinds, {("changed", "meas-r1"), ("missing", "curve")})

    def test_failed_or_unanalysed_pipelines_are_not_locked(self):
        self.make_analysed_store()
        (home.analysis_dir() / "meas" / "curve" / "provenance.json").write_text('{"exit_code": 1}')
        self.assertEqual(self.lock_data()["pipeline"], [])


class Sync(Env):
    def test_push_then_pull_into_an_empty_store_verifies_by_hash(self):
        self.make_analysed_store()
        spec = spec_mod.load(self.spec_path)
        data = lock.collect(spec, home.runs_dir(), home.analysis_dir() / "meas")
        remote = self.root / "remote"
        self.assertEqual(sync.push(str(remote), data, home.store_root(), log=lambda m: None), 0)
        self.assertTrue((remote / "runs" / "meas" / "meas-r1" / "manifest.json").is_file())

        fresh = self.root / "fresh"
        self.assertEqual(
            len(lock.check(data, fresh / "runs", fresh / "analysis" / "meas")), 2
        )  # run and output
        problems = sync.pull(str(remote), data, fresh, log=lambda m: None)
        self.assertEqual(problems, [])

    def test_a_tampered_remote_copy_is_reported_not_trusted(self):
        self.make_analysed_store()
        spec = spec_mod.load(self.spec_path)
        data = lock.collect(spec, home.runs_dir(), home.analysis_dir() / "meas")
        remote = self.root / "remote"
        sync.push(str(remote), data, home.store_root(), log=lambda m: None)
        with (remote / "runs" / "meas" / "meas-r1" / "records.jsonl").open("a") as f:
            f.write("tampered\n")
        problems = sync.pull(str(remote), data, self.root / "fresh", log=lambda m: None)
        self.assertTrue(any(kind == "changed" for kind, _, _ in problems))


if __name__ == "__main__":
    unittest.main()
