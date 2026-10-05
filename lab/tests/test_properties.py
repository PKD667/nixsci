import string
import unittest
from math import prod

from hypothesis import given
from hypothesis import strategies as st

from nixsci.lab import publish, store
from nixsci.lab import spec as spec_mod

WORD = st.text(alphabet=string.ascii_letters + string.digits + "-_./", min_size=1, max_size=20)
HEX = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
ENTRY = st.fixed_dictionaries(
    {"app": WORD, "run": WORD, "manifest_sha256": HEX},
    optional={"records_sha256": HEX, "replicate": st.integers(1, 9), "machine": WORD},
)
KEY = st.from_regex(r"[a-z]{1,6}", fullmatch=True)


class Lock(unittest.TestCase):
    @given(st.lists(ENTRY, max_size=8))
    def test_adding_runs_keeps_one_sorted_entry_per_name_and_is_idempotent(self, entries):
        lock: dict = {}
        for e in entries:
            lock = publish.add_entry(lock, e["app"], {"name": e["run"], "path": "/p/" + e["run"], "sha256": e["manifest_sha256"]})
        for app, listed in lock.items():
            names = [x["name"] for x in listed]
            self.assertEqual(names, sorted(set(names)))
        again = lock
        for e in entries:
            again = publish.add_entry(again, e["app"], {"name": e["run"], "path": "/p/" + e["run"], "sha256": e["manifest_sha256"]})
        self.assertEqual(again, lock)


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
        jobs = spec_mod.from_json({"kind": "experiment", "name": "e", "seeds": seeds, "sweep": sweep}).jobs()
        self.assertEqual(len(jobs), max(1, len(seeds)) * prod(len(v) for v in sweep.values()))
        self.assertEqual(len({(j.seed, tuple(sorted(j.params.items()))) for j in jobs}), len(jobs))


if __name__ == "__main__":
    unittest.main()
