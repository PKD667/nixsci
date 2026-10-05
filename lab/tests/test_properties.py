import json
import string
import tempfile
import tomllib
import unittest
from math import prod
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from nixsci.lab import analysis, lock, store
from nixsci.lab import spec as spec_mod

WORD = st.text(alphabet=string.ascii_letters + string.digits + "-_./", min_size=1, max_size=20)
HEX = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
ENTRY = st.fixed_dictionaries(
    {"app": WORD, "run": WORD, "manifest_sha256": HEX},
    optional={"records_sha256": HEX, "replicate": st.integers(1, 9), "machine": WORD},
)
KEY = st.from_regex(r"[a-z]{1,6}", fullmatch=True)


class Lock(unittest.TestCase):
    @given(WORD, st.lists(ENTRY, max_size=6, unique_by=lambda e: e["run"]))
    def test_a_lock_survives_being_written_and_read(self, app, runs):
        data = {"kind": "experiment", "app": app, "use": [], "pipeline": [], "run": runs}
        parsed = tomllib.loads(lock.dumps(data))
        self.assertEqual((parsed["kind"], parsed["app"]), ("experiment", app))
        self.assertEqual(parsed.get("run", []), runs)


class Identity(unittest.TestCase):
    @given(st.dictionaries(KEY, st.integers(), min_size=1, max_size=5), st.integers(1, 99))
    def test_input_id_ignores_key_order_and_sees_every_value(self, params, bump):
        ident = lambda p: store.input_id("m", "/nix/store/a-x", p, 0, {}, {})  # noqa: E731
        self.assertEqual(ident(params), ident(dict(reversed(list(params.items())))))
        key = next(iter(params))
        self.assertNotEqual(ident(params), ident({**params, key: params[key] + bump}))


class Jobs(unittest.TestCase):
    @given(
        st.lists(st.integers(0, 99), max_size=4, unique=True),
        st.dictionaries(KEY, st.lists(st.integers(), min_size=1, max_size=3, unique=True), max_size=3),
    )
    def test_the_jobs_are_the_product_of_the_seeds_and_the_sweep_and_all_differ(self, seeds, sweep):
        path = Path(tempfile.mkdtemp()) / "e.toml"
        table = "".join(f"{k} = {json.dumps(v)}\n" for k, v in sweep.items())
        path.write_text(f'[experiment]\nname = "e"\nseeds = {json.dumps(seeds)}\n[sweep]\n{table}')
        jobs = spec_mod.load(path).jobs()
        self.assertEqual(len(jobs), max(1, len(seeds)) * prod(len(v) for v in sweep.values()))
        self.assertEqual(len({(j.seed, tuple(sorted(j.params.items()))) for j in jobs}), len(jobs))


class View(unittest.TestCase):
    @settings(deadline=None, max_examples=20)
    @given(st.integers(1, 4), st.integers(0, 4))
    def test_runs_that_arrive_after_the_lock_never_enter_the_view(self, locked, later):
        root = Path(tempfile.mkdtemp())
        runs = root / "runs"
        (root / "e.toml").write_text('[experiment]\nname = "e"\n')
        (root / "a.toml").write_text('[analysis]\nname = "a"\n[use]\ne = "e.toml"\n')

        def add(name):
            directory = runs / "e" / name
            directory.mkdir(parents=True)
            (directory / "manifest.json").write_text(json.dumps({"app": "e", "run": name, "state": "ok"}))

        for i in range(locked):
            add(f"e-{i}")
        experiment = spec_mod.load(root / "e.toml")
        lock.write(experiment.path, lock.collect(experiment, runs, root / "analysis"))
        for i in range(later):
            add(f"late-{i}")
        resolved = analysis.resolve(spec_mod.load(root / "a.toml"), runs)
        analysis.build_view(resolved, runs, root / "data", root / "view")
        seen = [r["run"] for r in json.loads((root / "view" / "e" / "runs.json").read_text())]
        self.assertEqual(sorted(seen), [f"e-{i}" for i in range(locked)])


if __name__ == "__main__":
    unittest.main()
