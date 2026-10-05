import json
import os
import tempfile
import unittest
from pathlib import Path

from nixsci import lab
from nixsci.lab import schema
from nixsci.lab import spec as spec_mod

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


if __name__ == "__main__":
    unittest.main()
