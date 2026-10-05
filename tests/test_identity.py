import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import lab
from lab import schema
from nix_lab import spec as spec_mod
from nix_lab import store, verify

SPEC = textwrap.dedent("""
    [experiment]
    name = "meas"
    seeds = [0, 1]
    replicates = 2
    [data.size]
    columns = { n = "int", seconds = "float", note = "str" }
    key = ["n"]
    noisy = { seconds = 0.25 }
    """)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.spec_path = self.root / "meas.toml"
        self.spec_path.write_text(SPEC)
        self.runs = self.root / "runs"
        lab._seen.clear()

    def make_run(self, name, rows, **kw):
        with lab.Run(self.runs, name, spec=self.spec_path, seed=kw.get("seed", 0)) as run:
            for row in rows:
                run.record("size", row)
        return self.runs / "meas" / name


class Identity(Fixture):
    def test_input_id_depends_on_every_input_and_nothing_else(self):
        base = ("meas", "/nix/store/a-x", {"p": 1}, 3, {"d": {"c": "int"}}, {"d": ["c"]})
        same = store.input_id(*base)
        self.assertEqual(same, store.input_id(*base))
        for i, other in enumerate(["x", "/nix/store/b-x", {"p": 2}, 4, {}, {}], start=0):
            changed = list(base)
            changed[i] = other
            self.assertNotEqual(same, store.input_id(*changed), i)

    def test_plan_skips_satisfied_inputs_and_numbers_replicates_after_failures(self):
        spec = spec_mod.load(self.spec_path)
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

    def test_spec_validation(self):
        for bad in ("replicates = 0", "replicates = true", "replicates = 'x'"):
            path = self.root / "bad.toml"
            path.write_text(SPEC.replace("replicates = 2", bad))
            with self.assertRaises(ValueError):
                spec_mod.load(path)
        for noisy in ("{ n = 0.1 }", "{ note = 0.1 }", "{ nope = 0.1 }", "{ seconds = -1 }"):
            tables = {
                "size": {
                    "columns": {"n": "int", "seconds": "float", "note": "str"},
                    "key": ["n"],
                    "noisy": json.loads(json.dumps({})) or {},
                }
            }
            import tomllib

            tables["size"]["noisy"] = tomllib.loads(f"x = {noisy}")["x"]
            with self.assertRaises(ValueError):
                schema.tolerances(tables)


class Sealing(Fixture):
    def test_a_manual_run_is_sealed_with_hash_spec_machine_and_tolerance(self):
        d = self.make_run("r", [{"n": 1, "seconds": 1.0, "note": "a"}])
        m = json.loads((d / "manifest.json").read_text())
        self.assertEqual(m["records_sha256"], lab.run.sha256_file(d / "records.jsonl"))
        self.assertEqual(m["tolerance"], {"size": {"seconds": 0.25}})
        self.assertTrue((d / "spec.toml").is_file())
        self.assertEqual(len(m["spec_sha256"]), 64)
        self.assertIn("hostname", m["machine"])

    def test_compact_refuses_data_changed_after_the_run(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nix_lab.compact import compact

        d = self.make_run("r", [{"n": 1, "seconds": 1.0, "note": "a"}])
        compact(self.runs, self.root / "data")
        with (d / "records.jsonl").open("a") as f:
            f.write("\n")
        with self.assertRaises(ValueError):
            compact(self.runs, self.root / "data", force=True)

    def test_compact_is_incremental(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")
        from nix_lab.compact import compact

        self.make_run("r", [{"n": 1, "seconds": 1.0, "note": "a"}])
        self.assertEqual(len(compact(self.runs, self.root / "data")), 1)
        self.assertEqual(compact(self.runs, self.root / "data"), [])
        self.assertEqual(len(compact(self.runs, self.root / "data", force=True)), 1)


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

    def test_exact_columns_and_key_sets_must_match(self):
        a = self.make_run("a", [self.row])
        self.assertFalse(verify.compare(a, self.make_run("b", [{**self.row, "note": "z"}]))["ok"])
        self.assertFalse(verify.compare(a, self.make_run("c", [{**self.row, "n": 9}]))["ok"])
        self.assertFalse(
            verify.compare(a, self.make_run("d", [self.row, {**self.row, "n": 2}]))["ok"]
        )

    def test_bundle_collects_what_a_rerun_needs(self):
        d = self.make_run("a", [self.row])
        info = verify.bundle(d)
        self.assertEqual((info["run"], info["attr"], info["seed"]), ("a", "meas", 0))
        self.assertEqual(info["records_sha256"], lab.run.sha256_file(d / "records.jsonl"))
        self.assertIsNone(info["flake"])  # a hand-made run has no locked source

    def test_locked_ref_renders_through_nix(self):
        locked = {"type": "github", "owner": "o", "repo": "r", "rev": "a" * 40}
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode, run.return_value.stdout = 0, "github:o/r/" + "a" * 40
            self.assertEqual(verify.locked_ref(locked), "github:o/r/" + "a" * 40)
            self.assertIn("flakeRefToString", run.call_args[0][0][-1])

    def test_find_run_by_id(self):
        d = self.make_run("a", [self.row])
        self.assertEqual(verify.find_run("a", self.runs), d)
        self.assertEqual(verify.find_run(d), d)
        with self.assertRaises(SystemExit):
            verify.find_run("missing", self.runs)


if __name__ == "__main__":
    unittest.main()
