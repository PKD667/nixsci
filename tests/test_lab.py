import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

import lab
from lab import schema
from nix_lab import spec as spec_mod

SCHEMA = {"loss": {"epoch": "int", "value": "float", "split": "str?"}}


class Schema(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["NIX_LAB_DIR"] = self.dir
        os.environ["NIX_LAB_SCHEMA"] = json.dumps(SCHEMA)
        self.addCleanup(
            lambda: [os.environ.pop(k, None) for k in ("NIX_LAB_DIR", "NIX_LAB_SCHEMA")]
        )

    def test_valid_rows_are_recorded(self):
        lab.record("loss", {"epoch": 1, "value": 0.5, "split": None})
        lab.record("loss", {"epoch": 2, "value": 1, "split": "val"})
        rows = lab.load(self.dir, "loss")
        self.assertEqual([r["data"]["epoch"] for r in rows], [1, 2])

    def test_wrong_rows_are_refused_and_nothing_is_written(self):
        for bad in (
            {"epoch": 1, "value": 0.5},
            {"epoch": 1.5, "value": 0.5, "split": "a"},
            {"epoch": True, "value": 0.5, "split": "a"},
            {"epoch": 1, "value": "x", "split": "a"},
            {"epoch": 1, "value": 0.5, "split": "a", "extra": 1},
            [1, 2],
        ):
            with self.assertRaises((ValueError, TypeError)):
                lab.record("loss", bad)
        with self.assertRaises(ValueError):
            lab.record("undeclared", {"a": 1})
        self.assertEqual(lab.load(self.dir), [])

    def test_artifacts_stay_free_when_a_schema_is_declared(self):
        lab.record("blob", b"abc")
        self.assertEqual(lab.load(self.dir, "blob")[0]["kind"], "artifact")

    def test_bad_declarations_are_refused(self):
        for columns in ({"run": "int"}, {"x": "number"}, {}, {"bad name": "int"}):
            with self.assertRaises(ValueError):
                schema.parse("d", columns)


class SpecData(unittest.TestCase):
    def test_data_and_pipeline_tables_load(self):
        path = Path(tempfile.mkdtemp()) / "e.toml"
        path.write_text(textwrap.dedent("""
            [experiment]
            name = "e"
            [data.loss]
            columns = { epoch = "int", value = "float" }
            [pipeline.fig]
            script = "fig.R"
        """))
        s = spec_mod.load(path)
        self.assertEqual(s.data["loss"], {"epoch": "int", "value": "float"})
        self.assertEqual(s.pipelines["fig"], {"script": "fig.R", "inputs": ["e"]})


class Compact(unittest.TestCase):
    def test_runs_become_partitioned_parquet(self):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nix_lab.compact import compact

        root = Path(tempfile.mkdtemp())
        for i, seed in enumerate((0, 1)):
            run = root / "runs" / "e" / f"e-{i}"
            (run / "lab").mkdir(parents=True)
            os.environ["NIX_LAB_DIR"] = str(run / "lab")
            os.environ["NIX_LAB_SCHEMA"] = json.dumps(SCHEMA)
            lab.record("loss", {"epoch": 1, "value": 0.5 + i, "split": None})
            (run / "manifest.json").write_text(
                json.dumps(
                    {"app": "e", "run": f"e-{i}", "state": "ok", "seed": seed, "schema": SCHEMA}
                )
            )
        for k in ("NIX_LAB_DIR", "NIX_LAB_SCHEMA"):
            os.environ.pop(k)
        files = compact(root / "runs", root / "data")
        self.assertEqual(len(files), 2)
        table = pq.ParquetFile(files[1]).read()
        self.assertEqual(table.column_names, ["run", "seed", "time", "epoch", "value", "split"])
        self.assertEqual(table.to_pylist()[0]["value"], 1.5)


if __name__ == "__main__":
    unittest.main()
