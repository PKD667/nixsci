"""Where runs are counted and kept: the lock, the staging directory, and what a run is identified by.

These are the cases where two machines, a fresh clone or a changed schema used to make the executor
and the analysis disagree about which runs exist (see lab/README.md, "Replicates, identity ...").
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nixsci.deploy.providers import Lease
from nixsci.lab import publish, rows, runner, store
from nixsci.lab import spec as spec_mod

CLOSURE = "/nix/store/00000000000000000000000000000000-experiment-demo"
COLUMNS = {"epoch": "int", "value": "float"}


def spec_of(columns=COLUMNS, **extra):
    return spec_mod.from_json({
        "kind": "experiment", "name": "demo", "seeds": [0], "replicates": 1,
        "data": {"loss": {"columns": columns}}, **extra,
    })


def assign(job):
    return "t0", CLOSURE


def facts(spec, replicate, state="ok", closure=CLOSURE):
    job = spec.jobs()[0]
    ident = store.job_id(spec, closure, job)
    return {"run": f"demo-{ident[:12]}-r{replicate}", "state": state, "closure": closure, "seed": job.seed,
            "params": job.params, "input_id": ident, "replicate": replicate}


def staged(root, manifest):
    directory = Path(root) / "demo" / manifest["run"]
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(manifest))


class Identity(unittest.TestCase):
    def test_a_declared_span_is_part_of_the_identity_and_span_one_is_not(self):
        plain = store.job_id(spec_of(), CLOSURE, spec_of().jobs()[0])
        self.assertEqual(store.job_id(spec_of(span=1), CLOSURE, spec_of().jobs()[0]), plain)
        self.assertNotEqual(store.job_id(spec_of(span=4), CLOSURE, spec_of().jobs()[0]), plain)

    def test_a_schema_key_or_closure_change_is_a_new_input(self):
        base = store.current_ids(spec_of(), [CLOSURE])
        self.assertTrue(base.isdisjoint(store.current_ids(spec_of({**COLUMNS, "split": "str"}), [CLOSURE])))
        self.assertTrue(base.isdisjoint(store.current_ids(spec_of(), [CLOSURE + "2"])))

    def test_ids_are_what_the_ids_written_before_this_change_were(self):
        # A job of an ordinary experiment keeps the id its runs were already locked under.
        self.assertEqual(store.input_id("demo", CLOSURE, {}, 0, {"loss": COLUMNS}, {}),
                         "58aefe8b8a3688b001346e55ad1c74f4a53b597795b84cdf67d54b2fa6c57594")


class Planning(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.spec = spec_of()

    def test_a_locked_run_satisfies_its_input_on_a_machine_that_staged_nothing(self):
        # A fresh clone: lab.lock lists the run, .nixsci/runs is empty. Nothing is measured again.
        todo, satisfied = store.plan(self.spec, self.root, assign, locked=[facts(self.spec, 1)])
        self.assertEqual((todo, satisfied), ([], 1))

    def test_an_empty_lock_means_the_run_is_missing_however_much_is_staged(self):
        staged(self.root, facts(self.spec, 1))
        todo, satisfied = store.plan(self.spec, self.root, assign, locked=[])
        self.assertEqual((len(todo), satisfied), (1, 0))

    def test_without_a_lock_the_staged_runs_count(self):
        staged(self.root, facts(self.spec, 1))
        self.assertEqual(store.plan(self.spec, self.root, assign), ([], 1))

    def test_a_replicate_made_elsewhere_is_never_given_its_number_again(self):
        # Another machine's run r1 is in the lock; this one has staged nothing and needs a second replicate.
        two = spec_of(replicates=2)
        todo, _ = store.plan(two, self.root, assign, locked=[facts(two, 1)])
        self.assertEqual([replicate for *_, replicate in todo], [2])

    def test_numbers_also_continue_after_runs_only_staged_here(self):
        staged(self.root, facts(self.spec, 1, state="failed"))
        todo, _ = store.plan(self.spec, self.root, assign, locked=[])
        self.assertEqual([replicate for *_, replicate in todo], [2])


class Lock(unittest.TestCase):
    def entry(self, sha256, name="demo-1-r1"):
        return {"name": name, "path": f"/nix/store/x-{name}", "sha256": sha256}

    def test_the_same_run_again_is_idempotent(self):
        lock = publish.add_entry({}, "demo", self.entry("sha256-a"))
        self.assertEqual(publish.add_entry(lock, "demo", self.entry("sha256-a")), lock)

    def test_another_measurement_under_a_locked_name_is_refused_not_substituted(self):
        lock = publish.add_entry({}, "demo", self.entry("sha256-alice"))
        with self.assertRaisesRegex(ValueError, "other contents"):
            publish.add_entry(lock, "demo", self.entry("sha256-bob"))
        self.assertEqual(lock["demo"][0]["sha256"], "sha256-alice")

    def test_retain_keeps_what_the_current_ids_name_and_the_runs_recorded_by_hand(self):
        root = Path(tempfile.mkdtemp())
        old_schema, new_schema = spec_of(), spec_of({**COLUMNS, "split": "str"})
        runs = {"old": facts(old_schema, 1), "new": facts(new_schema, 1),
                "hand": {"run": "hand", "closure": None, "input_id": None}}
        lock = {"demo": [{"name": n, "path": "/x", "sha256": "x", "facts": f} for n, f in runs.items()]}
        (root / "lab.lock").write_text(json.dumps({"format": "2.0", "runs": lock}))
        dropped = publish.retain(root, "demo", store.current_ids(new_schema, [CLOSURE]))
        self.assertEqual(dropped, ["old"])
        self.assertEqual([e["name"] for e in publish.read_lock(root)["demo"]], ["new", "hand"])


class Rows(unittest.TestCase):
    def record(self, data, id="1-0", kind="value"):
        return {"id": id, "name": "loss", "kind": kind, "data": data, "time": "t", "tags": {}}

    def test_good_rows_pass(self):
        rows.validate("loss", COLUMNS, ["epoch"], [self.record({"epoch": 1, "value": 0.5}), self.record({"epoch": 2, "value": 0.5})])

    def test_a_wrong_type_a_missing_column_and_a_repeated_key_are_each_refused(self):
        for bad, message in [
            ([self.record({"epoch": 1.5, "value": 0.5})], "epoch"),
            ([self.record({"epoch": 1})], "missing"),
            ([self.record({"epoch": 1, "value": 0.5}), self.record({"epoch": 1, "value": 0.7})], "duplicate key"),
            ([self.record({}, kind="artifact")], "artifact"),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                rows.validate("loss", COLUMNS, ["epoch"], bad)

    def test_problems_reads_a_records_file_and_names_each_dataset_once(self):
        path = Path(tempfile.mkdtemp()) / "records.jsonl"
        path.write_text("".join(json.dumps(self.record(d, f"1-{i}")) + "\n"
                                for i, d in enumerate([{"epoch": 1, "value": 1.0}, {"epoch": 1, "value": 2.0}])))
        found = rows.problems(path, {"loss": COLUMNS}, {"loss": ["epoch"]})
        self.assertEqual(len(found), 1)
        self.assertIn("duplicate key", found[0])
        self.assertEqual(rows.problems(path.with_name("missing.jsonl"), {"loss": COLUMNS}, {}), [])


class Host:
    """A host whose job exited 0 and left `records` behind."""

    def __init__(self, records):
        self.records = records

    def stage(self, closure):
        pass

    def clear(self, run_id):
        return False

    def launch(self, closure, run_id, env):
        return {"run": run_id}

    def alive(self, handle):
        return False

    def exists(self, handle, name):
        return True

    def fetch(self, handle, name, dest):
        dest.mkdir(parents=True)
        (dest / "records.jsonl").write_text(self.records)

    def read_file(self, handle, path):
        return b'{"exit_code": 0}'

    def remove(self, handle):
        pass


class Executor(unittest.TestCase):
    def run_job(self, rows_written):
        records = "".join(json.dumps({"id": f"1-{i}", "name": "loss", "kind": "value", "data": d, "time": "t", "tags": {}}) + "\n"
                          for i, d in enumerate(rows_written))
        deploy = (SimpleNamespace(backend=lambda name, configs: Host(records)),
                  SimpleNamespace(acquire=lambda *a: Lease([{"system": "x86_64-linux"}], ["h0"], _release=lambda: None)))
        spec = spec_mod.from_json({"kind": "experiment", "name": "demo", "seeds": [0],
                                   "data": {"loss": {"columns": COLUMNS, "key": ["epoch"]}}})
        with mock.patch.object(runner, "_deploy", lambda: deploy):
            return runner.run(spec, {"x86_64-linux": SimpleNamespace(path=CLOSURE, source="{}")},
                              Path(tempfile.mkdtemp()), poll=0, log=lambda _: None)[0]

    def test_a_run_whose_rows_compaction_would_refuse_is_failed_here_not_locked(self):
        manifest = self.run_job([{"epoch": 1, "value": 1.0}, {"epoch": 1, "value": 2.0}])
        self.assertEqual(manifest["state"], "failed")
        self.assertIn("invalid rows: dataset 'loss': duplicate key (1,)", manifest["health"])

    def test_good_rows_are_accepted(self):
        self.assertEqual(self.run_job([{"epoch": 1, "value": 1.0}, {"epoch": 2, "value": 2.0}])["state"], "ok")


if __name__ == "__main__":
    unittest.main()
