"""A job that exits 0 can still have failed: its rows say so. nixsci notices without being asked.

Grid'5000 history: 69 of 69 launches died in 0.05 s (a launcher misconfiguration), the runner exited 0 because
it records failures as data, and nixsci printed "1/1 new run(s) ok" and offered the run for lab.lock. The rule is
a convention, not a configuration: a dataset with a boolean column named `ok` says, row by row, whether it worked."""
import json
import unittest

from nixsci.lab import health

SCHEMA = {"run": {"columns": {"program": "str", "ok": "bool", "verdict": "str"}},
          "cfg": {"columns": {"text": "str"}}}


def rows(dataset, *oks, verdict="exit"):
    return [{"name": dataset, "data": {"program": f"p{i}", "ok": ok, "verdict": "ok" if ok else verdict}}
            for i, ok in enumerate(oks)]


class Judge(unittest.TestCase):
    def test_all_rows_not_ok_is_a_failed_run_in_plain_words(self):
        failed, notes = health.judge(rows("run", *[False] * 69), SCHEMA)
        self.assertTrue(failed)
        self.assertEqual(notes, ["all 69 rows of 'run' have ok=false (verdict: exit 69)"])

    def test_some_rows_not_ok_is_a_note_not_a_failure(self):
        # a failing program is a result, not a broken experiment
        failed, notes = health.judge(rows("run", True, True, False), SCHEMA)
        self.assertFalse(failed)
        self.assertEqual(notes, ["1 of 3 rows of 'run' have ok=false (verdict: exit 1)"])

    def test_all_ok_says_nothing(self):
        self.assertEqual(health.judge(rows("run", True, True), SCHEMA), (False, []))

    def test_datasets_without_an_ok_column_are_not_judged(self):
        data = [{"name": "cfg", "data": {"text": "x"}}]
        self.assertEqual(health.judge(data, {"cfg": SCHEMA["cfg"]}), (False, []))

    def test_an_ok_dataset_with_no_rows_is_noted_not_failed(self):
        # some experiments legitimately record nothing for some parameters
        failed, notes = health.judge([], SCHEMA)
        self.assertFalse(failed)
        self.assertEqual(notes, ["the job recorded no rows of 'run'"])

    def test_verdicts_are_summarised_when_there_are_several(self):
        data = rows("run", False, False, False, verdict="timeout") + rows("run", False, verdict="exit")
        _, notes = health.judge(data, SCHEMA)
        self.assertIn("verdict: timeout 3, exit 1", notes[0])

    def test_reads_a_records_file(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "records.jsonl"
            p.write_text("".join(json.dumps(r) + "\n" for r in rows("run", False, False)))
            failed, notes = health.check(p, SCHEMA)
        self.assertTrue(failed)
        self.assertIn("all 2 rows of 'run'", notes[0])
        self.assertEqual(health.check(pathlib.Path("/nonexistent/records.jsonl"), SCHEMA)[0], False)


if __name__ == "__main__":
    unittest.main()
