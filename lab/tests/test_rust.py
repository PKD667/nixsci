"""The Rust crate and the Python module must agree on the format.

Set NIX_LAB_RUST_EMIT to the built `emit` example (cargo build --example emit); skipped otherwise.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest

from nixsci import lab
from nixsci.lab import schema

COLUMNS = {"loss": {"epoch": "int", "split": "str", "value": "float"}}
EMIT = os.environ.get("NIX_LAB_RUST_EMIT")


@unittest.skipUnless(EMIT, "NIX_LAB_RUST_EMIT not set")
class RustWritesWhatPythonReads(unittest.TestCase):
    def test_round_trip(self):
        directory = tempfile.mkdtemp()
        env = {
            **os.environ,
            "NIX_LAB_DIR": directory,
            "NIX_LAB_SCHEMA": json.dumps(COLUMNS),
            "NIX_LAB_KEYS": json.dumps({"loss": ["epoch", "split"]}),
            "NIX_LAB_PARAMS": '{"epsilon": 0.3}',
            "NIX_LAB_SEED": "7",
        }
        out = subprocess.run([EMIT], env=env, capture_output=True, text=True, check=True)
        self.assertIn("seed=Some(7)", out.stdout)
        rows = lab.load(directory)
        self.assertEqual([r["name"] for r in rows], ["loss", "loss", "loss", "blob"])
        parsed = schema.parse("loss", COLUMNS["loss"])
        for row in rows[:3]:
            schema.check("loss", parsed, row["data"])
            self.assertEqual(row["v"], 1)
            self.assertRegex(row["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
        self.assertAlmostEqual(rows[1]["data"]["value"], 0.15)
        self.assertEqual(lab.artifact(directory, rows[3]), b"abc")
        self.assertEqual(rows[3]["bytes"], 3)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", rows[3]["sha256"]))


if __name__ == "__main__":
    unittest.main()
