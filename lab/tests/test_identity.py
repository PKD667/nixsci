import json
import tempfile
import unittest
from pathlib import Path

from nixsci import lab
from nixsci.lab import spec as spec_mod
from nixsci.lab import store, verify

SPEC = json.dumps(
    {
        "kind": "experiment", "name": "meas", "seeds": [0, 1], "replicates": 2,
        "data": {"size": {"columns": {"n": "int", "seconds": "float", "note": "str"}, "key": ["n"], "noisy": {"seconds": 0.25}}},
    }
)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.spec_path = self.root / "meas.spec.json"
        self.spec_path.write_text(SPEC)
        self.runs = self.root / "runs"
        lab._seen.clear()

    def make_run(self, name, rows, **kw):
        with lab.Run(self.runs, name, spec=self.spec_path, seed=kw.get("seed", 0)) as run:
            for row in rows:
                run.record("size", row)
        return self.runs / "meas" / name


class Identity(Fixture):
    def test_plan_skips_satisfied_inputs_and_numbers_replicates_after_failures(self):
        spec = spec_mod.read(self.spec_path)
        assign = lambda job: ("t0", "/nix/store/a-x")  # noqa: E731
        todo, satisfied = store.plan(spec, self.runs, assign)
        self.assertEqual((len(todo), satisfied), (4, 0))  # 2 seeds x 2 replicates
        self.assertEqual(sorted(r for *_, r in todo), [1, 1, 2, 2])

        ident0 = todo[0][2]
        for name, state in (("meas-a-r1", "ok"), ("meas-a-r2", "ok"), ("meas-b-r3", "failed")):
            d = self.runs / "meas" / name
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(json.dumps({"input_id": ident0, "state": state}))
        todo, satisfied = store.plan(spec, self.runs, assign)
        self.assertEqual(satisfied, 1)
        self.assertEqual(len(todo), 2)  # only the other seed remains
        todo, _ = store.plan(spec, self.runs, assign, again=True)
        again = [t for t in todo if t[2] == ident0]
        self.assertEqual([t[3] for t in again], [4])  # continues after 3 existing runs



class Sealing(Fixture):
    def test_compact_refuses_data_changed_after_the_run(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nixsci.lab.compact import compact_run

        d = self.make_run("r", [{"n": 1, "seconds": 1.0, "note": "a"}])
        compact_run(d, self.root / "data")
        with (d / "records.jsonl").open("a") as f:
            f.write("\n")
        with self.assertRaises(ValueError):
            compact_run(d, self.root / "data2")



class Comparison(Fixture):
    row = {"n": 1, "seconds": 1.0, "note": "a"}

    def test_noise_inside_the_tolerance_passes_and_outside_fails(self):
        a = self.make_run("a", [self.row, {"n": 2, "seconds": 2.0, "note": "b"}])
        ok = self.make_run(
            "ok", [{**self.row, "seconds": 1.2}, {"n": 2, "seconds": 2.0, "note": "b"}]
        )
        bad = self.make_run(
            "bad", [{**self.row, "seconds": 2.0}, {"n": 2, "seconds": 2.0, "note": "b"}]
        )
        self.assertTrue(verify.compare(a, ok)["ok"])
        report = verify.compare(a, bad)
        self.assertFalse(report["ok"])
        self.assertIn("seconds", report["datasets"]["size"]["problems"][0])
        self.assertAlmostEqual(report["datasets"]["size"]["worst_relative"]["seconds"], 0.5)



if __name__ == "__main__":
    unittest.main()
