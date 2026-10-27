import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nixsci.deploy.providers import Lease
from nixsci.lab import runner
from nixsci.lab import spec as spec_mod

CLOSURE = SimpleNamespace(path="/nix/store/x-demo", source="{}")


class Backend:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    def stage(self, closure):
        self.calls.append(("stage", self.name))

    def clear(self, run_id):
        return False

    def launch(self, closure, run_id, env):
        self.calls.append(("launch", self.name))
        return {"run": run_id}

    def alive(self, handle):
        return False

    def exists(self, handle, name):
        return False

    def read_file(self, handle, path):
        return b'{"exit_code": 0}'

    def stop(self, handle):
        pass

    def remove(self, handle):
        pass


class Fake:
    """A factory and a provider with four targets t0..t3 on hosts h0..h3."""

    def __init__(self):
        self.calls = []
        self.factory = SimpleNamespace(backend=lambda name, configs: Backend(name, self.calls))
        self.providers = SimpleNamespace(acquire=self.acquire)

    def acquire(self, name, resources, opts):
        return Lease([{"system": "x86_64-linux"} for _ in range(4)], [f"h{i}" for i in range(4)], _release=lambda: None)

    def group_launch(self, backends, hosts, closure, *, run_id, env):
        self.calls.append(("group", [b.name for b in backends], list(hosts)))
        return {"run": run_id}


def spec_of(**extra):
    raw = {
        "kind": "experiment",
        "name": "span-demo",
        "sweep": {"x": [1, 2, 3]},
        "resources": {"provider": "fake"},
        **extra,
    }
    return spec_mod.from_json(raw)


class SpanSpec(unittest.TestCase):
    def test_span_defaults_to_one_and_bad_values_are_refused(self):
        self.assertEqual(spec_of().span, 1)
        self.assertEqual(spec_of(span=4).span, 4)
        for bad in (0, -1, True, 2.0, "2"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                spec_of(span=bad)


class SpanRun(unittest.TestCase):
    def run_with(self, spec):
        fake = Fake()
        out = Path(tempfile.mkdtemp())
        with mock.patch.object(runner, "_deploy", lambda: (fake.factory, fake.providers)), \
                mock.patch("nixsci.deploy.group.launch", fake.group_launch):
            manifests = runner.run(spec, {"x86_64-linux": CLOSURE}, out, poll=0, log=lambda _: None)
        return fake, manifests

    def test_span_two_runs_three_jobs_on_slices_of_the_lease(self):
        fake, manifests = self.run_with(spec_of(span=2))
        by_x = {m["params"]["x"]: m for m in manifests}
        self.assertEqual(
            {x: (m["target"], m["hosts"], m["span"]) for x, m in by_x.items()},
            {1: ("t0", ["h0", "h1"], 2), 2: ("t2", ["h2", "h3"], 2), 3: ("t0", ["h0", "h1"], 2)},
        )
        groups = sorted(c for c in fake.calls if c[0] == "group")
        self.assertEqual(
            groups,
            sorted([("group", ["t0", "t1"], ["h0", "h1"]), ("group", ["t2", "t3"], ["h2", "h3"]),
                    ("group", ["t0", "t1"], ["h0", "h1"])]),
        )
        self.assertFalse([c for c in fake.calls if c[0] == "launch"])

    def test_span_one_manifest_has_no_span_or_hosts(self):
        fake, manifests = self.run_with(spec_of())
        self.assertEqual(len(manifests), 3)
        for m in manifests:
            self.assertNotIn("span", m)
            self.assertNotIn("hosts", m)
        self.assertFalse([c for c in fake.calls if c[0] == "group"])

    def test_span_larger_than_the_lease_fails_before_any_launch(self):
        fake = Fake()
        out = Path(tempfile.mkdtemp())
        with mock.patch.object(runner, "_deploy", lambda: (fake.factory, fake.providers)), \
                mock.patch("nixsci.deploy.group.launch", fake.group_launch):
            with self.assertRaisesRegex(SystemExit, "span 5 needs 5 hosts"):
                runner.run(spec_of(span=5), {"x86_64-linux": CLOSURE}, out, poll=0, log=lambda _: None)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
