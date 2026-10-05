import base64
import hashlib
import os
import stat
import tempfile
import unittest
import unittest.mock

from hypothesis import given
from hypothesis import strategies as st

from nixsci.deploy import providers
from nixsci.deploy.backend import Backend
from nixsci.deploy.model import Closure
from nixsci.deploy.ssh import SSH

HOST = st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True)


class StaticHosts(unittest.TestCase):
    @given(st.lists(HOST, min_size=1, max_size=8, unique=True))
    def test_every_listed_host_becomes_a_target_and_all_share_one_store(self, hosts):
        lease = providers.Static().acquire(
            providers.Resources(hosts=len(hosts)), {"hosts": ",".join(hosts), "user": "me"}
        )
        self.assertEqual(lease.hosts, hosts)
        self.assertEqual([t["host"] for t in lease.targets], [f"me@{h}" for h in hosts])
        self.assertEqual(len({(t["store"], t["run_root"]) for t in lease.targets}), 1)

    def test_there_is_no_scheduler(self):
        self.assertEqual(sorted(providers._BUILTIN), ["local", "static"])


class Bootstrap(unittest.TestCase):
    def test_a_wrong_pinned_hash_for_a_reference_is_refused(self):
        path = os.path.join(tempfile.mkdtemp(), "nix")
        with open(path, "wb") as f:
            f.write(b"#!/bin/sh\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        self.assertNotEqual(hashlib.sha256(open(path, "rb").read()).hexdigest(), "0" * 64)
        with unittest.mock.patch("nixsci.deploy.backend.resolve_bootstrap", lambda ref, nix="nix": path):
            with self.assertRaises(ValueError):
                SSH(
                    host="me@a",
                    system="x86_64-linux",
                    store="/s",
                    run_root="/r",
                    remote_bootstrap="/b/nix",
                    bootstrap="nixpkgs#nixStatic",
                    bootstrap_sha256="0" * 64,
                )


STORE = "/nix/store/" + "a" * 32 + "-exp"


class Capture(Backend):
    """A backend that stops at the point where the job's environment is final."""

    def __init__(self):
        self.env = None

    def _admit(self, closure): pass
    def _verify(self, closure): pass
    def _workdir(self, run_id): return f"/run/x/{run_id}"
    def _stage_inputs(self, workdir, records, existing=False): return {}, []
    def home(self): return None
    def enter(self, closure): return None
    def _write_json(self, workdir, name, value): pass
    def _program(self, closure, name): return STORE + "/bin/run"

    def _spawn(self, closure, workdir, run_id, executable, argv, env):
        self.env = env
        return {}


class Launch(unittest.TestCase):
    def test_a_job_never_writes_bytecode_into_the_immutable_store(self):
        backend = Capture()
        zero = "sha256-" + base64.b64encode(bytes(32)).decode()
        backend.launch(Closure(STORE, STORE + "/bin/run", {}, {STORE: zero}, "src", "x86_64-linux"), run_id="r1")
        self.assertEqual(backend.env["PYTHONDONTWRITEBYTECODE"], "1")


if __name__ == "__main__":
    unittest.main()
