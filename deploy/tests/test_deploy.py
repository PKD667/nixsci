import base64
import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
import unittest.mock

from hypothesis import given
from hypothesis import strategies as st

from nixsci.deploy import providers
from nixsci.deploy.backend import Backend, normalize_manifest
from nixsci.deploy.model import Closure
from nixsci.deploy.native import Native
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

    def test_local_target_declares_a_private_store(self):
        target = providers.Local().acquire(providers.Resources(), {}).targets[0]
        self.assertTrue(target["rootless"])
        self.assertNotEqual(target["store"], "/nix/store")

    def test_global_store_and_non_rootless_modes_are_rejected(self):
        for store, rootless in (("/tmp/private-store", False), ("/nix/store", True)):
            with self.subTest(store=store, rootless=rootless), self.assertRaises(ValueError):
                SSH(
                    host="me@a",
                    system="x86_64-linux",
                    store=store,
                    run_root="/tmp/runs",
                    remote_bootstrap="/tmp/bin/nix",
                    rootless=rootless,
                )

    def test_native_rejects_host_root(self):
        with unittest.mock.patch("nixsci.deploy.native.os.getuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "unprivileged host user"):
                Native(
                    system="x86_64-linux",
                    store="/tmp/private-store",
                    run_root="/tmp/runs",
                    rootless=True,
                )


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


class Executable(unittest.TestCase):
    """The ssh and scp executables are configuration: a word list or one string, never letters."""

    def setUp(self):  # no nix here: the default bootstrap is resolved to a file that exists
        path = os.path.join(tempfile.mkdtemp(), "nix")
        with open(path, "wb") as f:
            f.write(b"#!/bin/sh\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        patch = unittest.mock.patch("nixsci.deploy.backend.resolve_bootstrap",
                                    lambda ref, nix="nix": path)
        patch.start()
        self.addCleanup(patch.stop)

    def make(self, **kw):
        return SSH(host="me@a", system="x86_64-linux", store="/s", run_root="/r",
                   remote_bootstrap="/b/nix", **kw)

    def test_a_string_is_split_like_a_shell_and_a_list_is_kept(self):
        b = self.make(ssh_command="env JOB=7 oarsh", scp_command=["env", "JOB=7", "oarcp", "-q"])
        self.assertEqual(b._ssh[:3], ("env", "JOB=7", "oarsh"))
        self.assertEqual(b._scp[:4], ("env", "JOB=7", "oarcp", "-q"))
        self.assertEqual(self.make(ssh_command="my ssh")._ssh[:2], ("my", "ssh"))
        self.assertEqual(self.make(ssh_command="'my ssh'")._ssh[0], "my ssh")

    def test_the_default_is_plain_ssh_and_scp(self):
        b = self.make()
        self.assertEqual((b._ssh[0], b._scp[:2]), ("ssh", ("scp", "-q")))

    def test_nothing_that_is_not_an_executable_is_accepted(self):
        for bad in ("", "   ", [], [""], [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.make(ssh_command=bad)

    def test_every_remote_call_goes_through_the_configured_executable(self):
        b = self.make(ssh_command="WRAP ssh", ready_timeout=1)
        seen = []
        with unittest.mock.patch("subprocess.run", lambda argv, *a, **k: seen.append(list(argv))
                                 or subprocess.CompletedProcess(argv, 0, b"", b"")):
            b._wait_ready()
            b.exists({"workdir": "/r/x"}, ".")
        self.assertTrue(seen and all(a[:2] == ["WRAP", "ssh"] for a in seen), seen)


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


class Manifest(unittest.TestCase):
    def test_v2_walltime_and_restart_validation(self):
        paths = {STORE: "sha256-" + base64.b64encode(bytes(32)).decode()}
        base = {"format": "2.0", "program": STORE + "/bin/run"}
        valid = normalize_manifest({**base, "walltime": 0.25,
                                   "restart": {"policy": "always", "max": 2, "delay": 0.5}}, paths)
        self.assertEqual(valid["argv"], [])
        self.assertEqual(normalize_manifest(base, paths)["restart"]["delay"], 5)
        for fields in ({"walltime": 0}, {"walltime": float("inf")},
                       {"restart": {"policy": "maybe"}},
                       {"restart": {"max": -1}}, {"restart": {"delay": -1}},
                       {"format": "3.0"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                normalize_manifest({**base, **fields}, paths)


class Launch(unittest.TestCase):
    def test_a_job_never_writes_bytecode_into_the_immutable_store(self):
        backend = Capture()
        zero = "sha256-" + base64.b64encode(bytes(32)).decode()
        backend.launch(Closure(STORE, STORE + "/bin/run", {}, {STORE: zero}, "src", "x86_64-linux"), run_id="r1")
        self.assertEqual(backend.env["PYTHONDONTWRITEBYTECODE"], "1")


if __name__ == "__main__":
    unittest.main()
