import json
import os
import tempfile
import unittest
from pathlib import Path

from nixsci import lab
from nixsci.lab import schema
from nixsci.lab import spec as spec_mod

SCHEMA = {"loss": {"epoch": "int", "value": "float", "split": "str?"}}


class Schema(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["NIX_LAB_DIR"] = self.dir
        os.environ["NIX_LAB_SCHEMA"] = json.dumps(SCHEMA)
        self.addCleanup(
            lambda: [os.environ.pop(k, None) for k in ("NIX_LAB_DIR", "NIX_LAB_SCHEMA")]
        )

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



class Specs(unittest.TestCase):
    def test_a_spec_is_an_experiment_with_known_fields_and_well_formed_values(self):
        ok = {"kind": "experiment", "name": "e", "seeds": [0, 1], "sweep": {"x": [1, 2]}}
        self.assertEqual(len(spec_mod.from_json(ok).jobs()), 4)
        for bad in (
            {**ok, "kind": "analysis"},
            {**ok, "name": "1bad"},
            {**ok, "pipelines": {}},
            {**ok, "seeds": ["0"]},
            {**ok, "sweep": {"x": []}},
            {**ok, "replicates": 0},
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                spec_mod.from_json(bad)


class Compact(unittest.TestCase):
    def test_runs_become_partitioned_parquet(self):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nixsci.lab.compact import compact_run

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
        (file,) = compact_run(root / "runs" / "e" / "e-1", root / "out")
        table = pq.ParquetFile(file).read()
        self.assertEqual(table.column_names, ["run", "seed", "time", "epoch", "value", "split"])
        self.assertEqual(table.to_pylist()[0]["value"], 1.5)
        self.assertEqual(json.loads((root / "out" / "row.json").read_text())["seed"], 1)


if __name__ == "__main__":
    unittest.main()
