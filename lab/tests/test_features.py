import json
import os
import tempfile
import unittest

from nixsci import lab

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



if __name__ == "__main__":
    unittest.main()
