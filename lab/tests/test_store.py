import json
import os
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

from nixsci import lab
from nixsci.lab import home
from nixsci.lab import lock, spec as spec_mod, sync
from nixsci.lab.run import sha256_file

EXPERIMENT = textwrap.dedent("""
    [experiment]
    name = "meas"
    [data.size]
    columns = { n = "int", seconds = "float" }
    key = ["n"]
    """)
ANALYSIS = textwrap.dedent("""
    [analysis]
    name = "fig"
    [use]
    meas = "meas.toml"
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
        self.analysis_path = self.root / "proj" / "fig.toml"
        self.spec_path.parent.mkdir()
        self.spec_path.write_text(EXPERIMENT)
        self.analysis_path.write_text(ANALYSIS)
        (self.spec_path.parent / "curve.R").write_text("# script\n")

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def measured(self):
        """A finished run in the store and the experiment's lock, as `build` leaves them."""
        with lab.Run(None, "meas-r1", spec=self.spec_path, seed=1) as run:
            run.record("size", {"n": 1, "seconds": 2.0})
        spec = spec_mod.load(self.spec_path)
        lock.write(self.spec_path, lock.collect(spec, home.runs_dir(), home.analysis_dir() / "meas"))

    def make_analysed_store(self):
        """The measured store plus a successful pipeline output, as `build` leaves it."""
        self.measured()
        manifest = home.runs_dir() / "meas" / "meas-r1" / "manifest.json"
        out = home.analysis_dir() / "fig" / "curve"
        out.mkdir(parents=True)
        (out / "table.csv").write_text("n,seconds\n1,2.0\n")
        (out / "provenance.json").write_text(
            json.dumps(
                {
                    "exit_code": 0,
                    "fingerprint": "f" * 8,
                    "script_sha256": "s" * 8,
                    "inputs": [
                        {"alias": "meas", "run": "meas-r1", "manifest_sha256": sha256_file(manifest)}
                    ],
                }
            )
        )

    def analysis_lock(self):
        spec = spec_mod.load(self.analysis_path)
        return lock.collect(spec, home.runs_dir(), home.analysis_dir() / "fig")


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


class ExperimentLock(Env):
    def test_collect_write_read_roundtrip(self):
        self.measured()
        data = lock.read(self.spec_path)
        self.assertEqual((data["kind"], data["app"]), ("experiment", "meas"))
        self.assertEqual([r["run"] for r in data["run"]], ["meas-r1"])
        self.assertEqual(data["pipeline"], [])
        written = lock.path_for(self.spec_path)
        self.assertEqual(written.name, "meas.lab.lock")
        parsed = tomllib.loads(written.read_text())
        self.assertEqual(parsed["run"][0]["records_sha256"], data["run"][0]["records_sha256"])

    def test_only_finished_runs_are_locked(self):
        self.measured()
        bad = home.runs_dir() / "meas" / "meas-bad"
        bad.mkdir()
        (bad / "manifest.json").write_text('{"app": "meas", "run": "meas-bad", "state": "failed"}')
        spec = spec_mod.load(self.spec_path)
        data = lock.collect(spec, home.runs_dir(), home.analysis_dir() / "meas")
        self.assertEqual([r["run"] for r in data["run"]], ["meas-r1"])

    def test_check_reports_changed_records(self):
        self.measured()
        data = lock.read(self.spec_path)
        runs = home.runs_dir()
        self.assertEqual(lock.check(data, runs, Path(os.devnull)), [])
        with (runs / "meas" / "meas-r1" / "records.jsonl").open("a") as f:
            f.write("\n")
        self.assertEqual({k for k, _, _ in lock.check(data, runs, Path(os.devnull))}, {"changed"})


class AnalysisLock(Env):
    def test_it_pins_the_experiment_locks_it_uses_and_its_own_outputs(self):
        self.make_analysed_store()
        data = self.analysis_lock()
        self.assertEqual(data["kind"], "analysis")
        self.assertEqual(
            data["use"],
            [
                {
                    "alias": "meas",
                    "spec": "meas.toml",
                    "lock_sha256": sha256_file(lock.path_for(self.spec_path)),
                }
            ],
        )
        self.assertEqual([r["run"] for r in data["run"]], ["meas-r1"])
        self.assertEqual(data["pipeline"][0]["runs"], ["meas-r1"])
        self.assertEqual(list(data["pipeline"][0]["outputs"]), ["table.csv"])
        lock.write(self.analysis_path, data)
        self.assertEqual(lock.read(self.analysis_path)["use"][0]["alias"], "meas")

    def test_it_cannot_be_locked_before_the_experiment_is(self):
        with self.assertRaises(SystemExit) as stop:
            self.analysis_lock()
        self.assertIn("no lock file", str(stop.exception))

    def test_check_distinguishes_missing_from_changed(self):
        self.make_analysed_store()
        data = self.analysis_lock()
        runs, analysis = home.runs_dir(), home.analysis_dir() / "fig"
        self.assertEqual(lock.check(data, runs, analysis), [])
        with (runs / "meas" / "meas-r1" / "records.jsonl").open("a") as f:
            f.write("\n")
        (analysis / "curve" / "table.csv").unlink()
        kinds = {(k, w.split("/")[0]) for k, w, _ in lock.check(data, runs, analysis)}
        self.assertEqual(kinds, {("changed", "meas-r1"), ("missing", "curve")})

    def test_failed_pipelines_are_not_locked(self):
        self.make_analysed_store()
        (home.analysis_dir() / "fig" / "curve" / "provenance.json").write_text('{"exit_code": 1}')
        self.assertEqual(self.analysis_lock()["pipeline"], [])


class Sync(Env):
    def test_push_then_pull_into_an_empty_store_verifies_by_hash(self):
        self.make_analysed_store()
        data = self.analysis_lock()
        remote = self.root / "remote"
        self.assertEqual(sync.push(str(remote), data, home.store_root(), log=lambda m: None), 0)
        self.assertTrue((remote / "runs" / "meas" / "meas-r1" / "manifest.json").is_file())

        fresh = self.root / "fresh"
        self.assertEqual(
            len(lock.check(data, fresh / "runs", fresh / "analysis" / "fig")), 2
        )  # run and output
        problems = sync.pull(str(remote), data, fresh, log=lambda m: None)
        self.assertEqual(problems, [])

    def test_a_tampered_remote_copy_is_reported_not_trusted(self):
        self.make_analysed_store()
        data = self.analysis_lock()
        remote = self.root / "remote"
        sync.push(str(remote), data, home.store_root(), log=lambda m: None)
        with (remote / "runs" / "meas" / "meas-r1" / "records.jsonl").open("a") as f:
            f.write("tampered\n")
        problems = sync.pull(str(remote), data, self.root / "fresh", log=lambda m: None)
        self.assertTrue(any(kind == "changed" for kind, _, _ in problems))


if __name__ == "__main__":
    unittest.main()
