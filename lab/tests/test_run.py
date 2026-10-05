import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from nixsci import lab

SPEC = textwrap.dedent("""
    [experiment]
    name = "meas"
    [data.size]
    columns = { n = "int", seconds = "float" }
    key = ["n"]
    """)


class ManualRun(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.spec = self.root / "meas.toml"
        self.spec.write_text(SPEC)
        lab._seen.clear()

    def test_a_block_writes_records_and_an_ok_manifest(self):
        with lab.Run(self.root / "runs", "night-1", spec=self.spec, seed=3, params={"x": 1}) as run:
            run.record("size", {"n": 10, "seconds": 0.5})
            run.record("size", {"n": 20, "seconds": 1})
        directory = self.root / "runs" / "meas" / "night-1"
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(
            (manifest["app"], manifest["state"], manifest["seed"], manifest["keys"]),
            ("meas", "ok", 3, {"size": ["n"]}),
        )
        self.assertEqual(manifest["schema"], {"size": {"n": "int", "seconds": "float"}})
        self.assertEqual([r["data"]["n"] for r in lab.load(directory, "size")], [10, 20])

    def test_compact_reads_a_manual_run(self):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nixsci.lab.compact import compact

        with lab.Run(self.root / "runs", "r", spec=self.spec, seed=5) as run:
            run.record("size", {"n": 1, "seconds": 2.5})
        (file,) = compact(self.root / "runs", self.root / "data")
        row = pq.ParquetFile(file).read().to_pylist()[0]
        self.assertEqual((row["run"], row["seed"], row["n"], row["seconds"]), ("r", 5, 1, 2.5))


if __name__ == "__main__":
    unittest.main()
