import json
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from nixsci.lab import lock
from nixsci.lab import spec as spec_mod
from nixsci.lab.analysis import analyze


class Analysis(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.runs = self.root / "runs"
        (self.root / "e.toml").write_text('[experiment]\nname = "e"\n')
        (self.root / "a.toml").write_text(textwrap.dedent("""
            [analysis]
            name = "a"
            [use]
            e = "e.toml"
            [pipeline.p]
            script = "p.R"
            deps = ["helper.R"]
            """))
        (self.root / "p.R").write_text("# script\n")
        (self.root / "helper.R").write_text("# helper\n")
        self.add_run("e-0")
        self.lock_experiment()
        self.counter, self.seen, self.env = (self.root / n for n in ("count", "seen", "env"))
        fake = self.root / "Rscript"
        fake.write_text(
            "#!/bin/sh\n[ \"$1\" = --version ] && exit 0\n"
            f"echo x >> {self.counter}\n"
            f"cat \"$NIX_LAB_VIEW/e/runs.json\" > {self.seen}\n"
            f"env | grep '^NIX_LAB_' | sort > {self.env}\n"
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        os.environ["NIX_LAB_RSCRIPT"] = str(fake)
        os.environ["NIX_LAB_STORE"] = str(self.root / "ambient-store")
        self.addCleanup(os.environ.pop, "NIX_LAB_RSCRIPT", None)
        self.addCleanup(os.environ.pop, "NIX_LAB_STORE", None)

    def add_run(self, name):
        directory = self.runs / "e" / name
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            json.dumps({"app": "e", "run": name, "state": "ok", "params": {"k": 1}})
        )

    def lock_experiment(self):
        spec = spec_mod.load(self.root / "e.toml")
        lock.write(spec.path, lock.collect(spec, self.runs, self.root / "analysis"))

    def run_it(self, **kw):
        spec = spec_mod.load(self.root / "a.toml")
        return analyze(spec, self.runs, self.root / "data", self.root / "out", **kw)

    def times(self):
        return len(self.counter.read_text().split()) if self.counter.exists() else 0

    def seen_runs(self):
        return [r["run"] for r in json.loads(self.seen.read_text())]

    def test_unchanged_inputs_skip_and_changes_rerun(self):
        self.assertEqual(self.run_it(), {"p": (0, False)})
        self.assertEqual(self.run_it(), {"p": (0, True)})
        self.assertEqual(self.times(), 1)
        (self.root / "helper.R").write_text("# changed dependency\n")
        self.assertEqual(self.run_it(), {"p": (0, False)})
        self.assertEqual(self.run_it(force=True), {"p": (0, False)})
        self.assertEqual(self.times(), 3)

    def test_a_run_that_arrives_after_the_lock_is_invisible(self):
        self.run_it()
        self.add_run("e-1")
        self.assertEqual(self.run_it(), {"p": (0, True)})
        self.run_it(force=True)
        self.assertEqual(self.seen_runs(), ["e-0"])
        self.lock_experiment()
        self.assertEqual(self.run_it(), {"p": (0, False)})
        self.assertEqual(self.seen_runs(), ["e-0", "e-1"])

    def test_the_script_is_told_the_view_and_the_output_and_nothing_about_the_store(self):
        self.run_it()
        names = {line.split("=")[0] for line in self.env.read_text().split()}
        self.assertTrue({"NIX_LAB_VIEW", "NIX_LAB_OUT"} <= names)
        self.assertTrue({"NIX_LAB_STORE", "NIX_LAB_DATA", "NIX_LAB_RUNS"}.isdisjoint(names))

    def test_missing_locked_data_stops_before_r_starts(self):
        (self.runs / "e" / "e-0" / "manifest.json").unlink()
        with self.assertRaises(SystemExit) as stop:
            self.run_it()
        self.assertIn("does not hold", str(stop.exception))
        self.assertEqual(self.times(), 0)



if __name__ == "__main__":
    unittest.main()
