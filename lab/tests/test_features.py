import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from nixsci import lab
from nixsci.lab import schema
from nixsci.lab import spec as spec_mod
from nixsci.lab.analyze import analyze

COLUMNS = {"loss": {"epoch": "int", "split": "str", "value": "float"}}


class Keys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ.update(
            NIX_LAB_DIR=self.dir,
            NIX_LAB_SCHEMA=json.dumps(COLUMNS),
            NIX_LAB_KEYS=json.dumps({"loss": ["epoch", "split"]}),
        )
        lab._seen.clear()
        self.addCleanup(
            lambda: [
                os.environ.pop(k, None) for k in ("NIX_LAB_DIR", "NIX_LAB_SCHEMA", "NIX_LAB_KEYS")
            ]
        )

    def test_a_repeated_key_is_refused_at_record_time(self):
        lab.record("loss", {"epoch": 1, "split": "train", "value": 0.5})
        lab.record("loss", {"epoch": 1, "split": "val", "value": 0.6})
        with self.assertRaises(ValueError):
            lab.record("loss", {"epoch": 1, "split": "train", "value": 0.4})
        self.assertEqual(len(lab.load(self.dir, "loss")), 2)

    def test_key_declarations_are_checked(self):
        parsed = schema.parse("d", {"a": "int", "b": "int?"})
        for bad in ([], ["zzz"], ["b"], ["a", "a"], "a"):
            with self.assertRaises(ValueError):
                schema.parse_key("d", parsed, bad)
        self.assertEqual(schema.parse_key("d", parsed, ["a"]), ("a",))

    def test_spec_loads_a_key(self):
        path = Path(tempfile.mkdtemp()) / "e.toml"
        path.write_text(
            '[experiment]\nname="e"\n[data.loss]\ncolumns={epoch="int",v="float"}\nkey=["epoch"]\n'
        )
        self.assertEqual(spec_mod.load(path).keys, {"loss": ["epoch"]})


class Incremental(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "e.toml").write_text(textwrap.dedent("""
                [experiment]
                name = "e"
                [pipeline.p]
                script = "p.R"
                deps = ["helper.R"]
                """))
        (self.root / "p.R").write_text("# script\n")
        (self.root / "helper.R").write_text("# helper\n")
        run = self.root / "runs" / "e" / "e-0"
        run.mkdir(parents=True)
        (run / "manifest.json").write_text('{"app": "e", "run": "e-0", "state": "ok"}')
        # A fake Rscript that counts how often it ran and succeeds.
        self.counter = self.root / "count"
        fake = self.root / "Rscript"
        fake.write_text(f'#!/bin/sh\n[ "$1" = --version ] && exit 0\necho x >> {self.counter}\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        os.environ["NIX_LAB_RSCRIPT"] = str(fake)
        self.addCleanup(os.environ.pop, "NIX_LAB_RSCRIPT", None)

    def run_it(self, **kw):
        spec = spec_mod.load(self.root / "e.toml")
        return analyze(spec, self.root / "runs", self.root / "data", self.root / "out", **kw)

    def runs(self):
        return len(self.counter.read_text().split()) if self.counter.exists() else 0

    def test_unchanged_inputs_skip_and_changes_rerun(self):
        self.assertEqual(self.run_it(), {"p": (0, False)})
        self.assertEqual(self.run_it(), {"p": (0, True)})
        self.assertEqual(self.runs(), 1)
        (self.root / "helper.R").write_text("# changed dependency\n")
        self.assertEqual(self.run_it(), {"p": (0, False)})
        (self.root / "runs" / "e" / "e-1").mkdir()
        (self.root / "runs" / "e" / "e-1" / "manifest.json").write_text("{}")
        self.assertEqual(self.run_it(), {"p": (0, False)})
        self.assertEqual(self.run_it(force=True), {"p": (0, False)})
        self.assertEqual(self.runs(), 4)


if __name__ == "__main__":
    unittest.main()
