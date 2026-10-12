"""Stage caches obey the target disk choice and are private per invocation.

These are command/lifecycle contract tests, not proof of remote filesystem or
CUDA behavior. The real CBP run separately tests namespace and driver access.
"""
import base64
import subprocess
import unittest
from unittest.mock import Mock, patch

from nixsci.deploy.model import Closure
from nixsci.deploy.ssh import SSH


STORE = "/nix/store/" + "a" * 32 + "-experiment"
STAGE = "/local/me/nixsci/runs/.stage"
PREFIX = STAGE + "/nar-" + "a" * 32 + "-"


class StageCache(unittest.TestCase):
    def setUp(self):
        self.closure = Closure(STORE, STORE + "/bin/run", {},
                               {STORE: "sha256-" + base64.b64encode(bytes(32)).decode()},
                               "committed-source", "x86_64-linux")
        # Bypass only bootstrap verification; no real Nix, SSH or SCP is run.
        self.backend = object.__new__(SSH)
        self.backend.run_root = "/local/me/nixsci/runs"
        self.backend.host = "me@node"
        self.backend.nix = "/pinned/nix"
        self.backend._scp = ("scp", "-q")
        self.backend._admit = Mock()
        self.backend._wait_ready = Mock()
        self.backend._ship_bootstrap = Mock()
        self.backend._verify = Mock()
        self.backend._remote_nix = Mock()
        self.commands = []
        self.parents = iter([PREFIX + "AAAAAAAA", PREFIX + "BBBBBBBB"])
        self.remote_output = None
        def remote(command):
            self.commands.append(command)
            output = self.remote_output if self.remote_output is not None else next(self.parents) if "mktemp" in command else ""
            return subprocess.CompletedProcess([], 0, (output + "\n").encode(), b"")
        self.backend._remote = remote
        self.cache = patch("nixsci.deploy.ssh.nix.copy_to_cache")
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def test_stage_uses_declared_disk_and_unique_private_directories(self):
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as scp:
            self.backend.stage(self.closure)
            self.backend.stage(self.closure)
        destinations = [c.args[0][-1] for c in scp.call_args_list]
        self.assertEqual(destinations, ["me@node:" + PREFIX + "AAAAAAAA/", "me@node:" + PREFIX + "BBBBBBBB/"])
        self.assertNotEqual(destinations[0], destinations[1])
        self.assertTrue(all("/tmp/" not in command for command in self.commands))
        self.assertTrue(all("umask 077" in c for c in self.commands if "mktemp" in c))
        self.backend._verify.assert_called_with(self.closure)

    def test_copy_failure_cleans_only_its_own_cache(self):
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"failed")):
            with self.assertRaisesRegex(RuntimeError, "cannot ship closure cache"):
                self.backend.stage(self.closure)
        self.assertEqual(self.commands[-1], "rm -rf -- " + PREFIX + "AAAAAAAA")
        self.backend._remote_nix.assert_not_called()
        self.backend._verify.assert_not_called()

    def test_import_failure_cleans_cache(self):
        self.backend._remote_nix.side_effect = RuntimeError("import failed")
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            with self.assertRaisesRegex(RuntimeError, "import failed"):
                self.backend.stage(self.closure)
        self.assertEqual(self.commands[-1], "rm -rf -- " + PREFIX + "AAAAAAAA")
        self.backend._verify.assert_not_called()

    def test_bad_remote_directory_is_refused_without_deleting_anything(self):
        for value in ["/tmp/cache", PREFIX + "AAAAAAAA/..", PREFIX + "AAAAAAAA\n/tmp/other", ""]:
            with self.subTest(value=value):
                self.commands.clear()
                self.remote_output = value
                with patch("nixsci.deploy.ssh.subprocess.run") as scp:
                    with self.assertRaisesRegex(RuntimeError, "outside the declared run root"):
                        self.backend.stage(self.closure)
                scp.assert_not_called()
                self.assertFalse(any(c.startswith("rm") for c in self.commands))


if __name__ == "__main__":
    unittest.main()
