"""Partial caches change transport only; complete target NAR verification stays.

These command/lifecycle tests do not prove remote filesystem or CUDA behavior.
The real CBP run separately qualifies namespace and driver access.
"""
import base64
import shlex
from dataclasses import replace
import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from nixsci.deploy.model import Closure
from nixsci.deploy.ssh import SSH

STORE = "/nix/store/" + "a" * 32 + "-experiment"
DEP = "/nix/store/" + "b" * 32 + "-dependency"
HASH = "sha256-" + base64.b64encode(bytes(32)).decode()
STAGE = "/local/me/nixsci/runs/.stage"
PREFIX = STAGE + "/nar-" + "a" * 32 + "-"


class CacheEncoding(unittest.TestCase):
    def test_transfer_uses_zstd_without_changing_the_closure_root(self):
        from nixsci.deploy.nix import copy_to_cache
        with tempfile.TemporaryDirectory() as directory:
            cache = str(Path(directory) / "cache")
            with patch("nixsci.deploy.nix.run") as run:
                copy_to_cache("/pinned/nix", cache, STORE)
            run.assert_called_once_with("/pinned/nix", None, "copy", "--no-check-sigs", "--to",
                                        "file://" + cache + "?compression=zstd", STORE)
            self.assertTrue(Path(cache).is_dir())

    def test_explicit_paths_export_nonrecursive(self):
        from nixsci.deploy.nix import copy_to_cache
        with tempfile.TemporaryDirectory() as directory:
            cache = str(Path(directory) / "cache")
            with patch("nixsci.deploy.nix.run") as run:
                copy_to_cache("/pinned/nix", cache, STORE, DEP, recursive=False)
            run.assert_called_once_with("/pinned/nix", None, "copy", "--no-check-sigs", "--no-recursive",
                                        "--to", "file://" + cache + "?compression=zstd", STORE, DEP)

    def test_empty_export_fails_before_creating_cache(self):
        from nixsci.deploy.nix import copy_to_cache
        with tempfile.TemporaryDirectory() as directory:
            cache = str(Path(directory) / "cache")
            with self.assertRaisesRegex(ValueError, "at least one"):
                copy_to_cache("/pinned/nix", cache, recursive=False)
            self.assertFalse(Path(cache).exists())


class StageCache(unittest.TestCase):
    def setUp(self):
        self.closure = Closure(STORE, STORE + "/bin/run", {}, {STORE: HASH},
                               "committed-source", "x86_64-linux")
        self.backend = object.__new__(SSH)
        self.backend.run_root = "/local/me/nixsci/runs"
        self.backend.host = "me@node"
        self.backend.nix = "/pinned/nix"
        self.backend.remote_bootstrap = "/remote/bin/nix"
        self.backend.store = "/local/me/nixsci/store"
        self.backend._scp = ("scp", "-q")
        self.backend._admit = Mock()
        self.backend._wait_ready = Mock()
        self.backend._ship_bootstrap = Mock()
        self.backend._verify = Mock()
        self.valid = {}
        self.import_error = None
        def remote_nix(*args):
            if args[0] == "path-info":
                return subprocess.CompletedProcess([], 0, json.dumps(self.valid).encode(), b"")
            if self.import_error:
                raise RuntimeError(self.import_error)
            return subprocess.CompletedProcess([], 0, b"", b"")
        self.backend._remote_nix = Mock(side_effect=remote_nix)
        self.commands = []
        self.parents = iter([PREFIX + "AAAAAAAA", PREFIX + "BBBBBBBB"])
        self.remote_output = None
        def remote(command):
            self.commands.append(command)
            if "--import" in command and self.import_error:
                raise RuntimeError(self.import_error)
            # A rootless target: a non-root uid, paths that resolve as given,
            # and a store root created 0700 and owned by that uid.
            if command == "id -u":
                return subprocess.CompletedProcess([], 0, b"1000\n", b"")
            if command.startswith("realpath -m --"):
                return subprocess.CompletedProcess([], 0, (shlex.split(command)[-1] + "\n").encode(), b"")
            if command.startswith("umask 077; mkdir -m 700") or "stat -c" in command:
                return subprocess.CompletedProcess([], 0, b"1000 700\n1000\n", b"")
            output = self.remote_output if self.remote_output is not None else next(self.parents) if "mktemp" in command else ""
            return subprocess.CompletedProcess([], 0, (output + "\n").encode(), b"")
        self.backend._remote = remote
        # The existing cases predate the cache fetch: no path is on the cache, so nothing changes.
        patch("nixsci.deploy.nix.cache_partition",
              side_effect=lambda paths, has=None: ([], list(paths), None)).start()
        self.addCleanup(patch.stopall)
        self.cache = patch("nixsci.deploy.ssh.nix.export_paths")
        self.export = self.cache.start()
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

    def test_all_cached_verifies_and_returns_without_export(self):
        self.valid = {STORE: {"narHash": HASH}}
        with patch("nixsci.deploy.ssh.subprocess.run") as scp:
            result = self.backend.stage(self.closure)
        self.assertEqual(result["closure"], {STORE: HASH})
        self.backend._remote_nix.assert_called_once_with("path-info", "--all", "--json")
        self.export.assert_not_called()
        scp.assert_not_called()
        self.assertFalse([c for c in self.commands if "--import" in c or "rm -rf" in c or "mktemp" in c])
        self.backend._verify.assert_called_once_with(self.closure)

    def test_partial_cache_exports_and_imports_only_missing_paths(self):
        self.closure = replace(self.closure, closure={STORE: HASH, DEP: HASH})
        self.valid = [{"path": DEP, "narHash": HASH}]
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            self.backend.stage(self.closure)
        self.assertEqual(self.export.call_args.args[2:], (STORE,))
        self.assertEqual(self.export.call_args.kwargs, {})
        self.assertTrue(any("--import < " + PREFIX + "AAAAAAAA/closure.export" in c
                            for c in self.commands))
        self.backend._remote_nix.assert_called_once_with("path-info", "--all", "--json")
        self.backend._verify.assert_called_once_with(self.closure)

    def test_empty_cache_exports_every_explicit_path_without_recursion(self):
        self.closure = replace(self.closure, closure={STORE: HASH, DEP: HASH})
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            self.backend.stage(self.closure)
        self.assertEqual(self.export.call_args.args[2:], (STORE, DEP))
        self.assertEqual(self.export.call_args.kwargs, {})
        self.assertTrue(any("--import < " + PREFIX + "AAAAAAAA/closure.export" in c
                            for c in self.commands))
        self.backend._remote_nix.assert_called_once_with("path-info", "--all", "--json")

    def test_preexisting_hash_mismatch_is_fatal_without_repair_or_export(self):
        self.valid = {STORE: {"narHash": "sha256-wrong"}}
        with patch("nixsci.deploy.ssh.subprocess.run") as scp:
            with self.assertRaisesRegex(RuntimeError, "preexisting target path NAR identity differs"):
                self.backend.stage(self.closure)
        self.export.assert_not_called()
        self.backend._verify.assert_not_called()
        scp.assert_not_called()
        self.assertFalse([c for c in self.commands if "--import" in c or "rm -rf" in c or "mktemp" in c])

    def test_query_error_is_fatal_not_an_empty_cache(self):
        self.backend._remote_nix.side_effect = RuntimeError("SSH query failed")
        with self.assertRaisesRegex(RuntimeError, "SSH query failed"):
            self.backend.stage(self.closure)
        self.export.assert_not_called()
        self.backend._verify.assert_not_called()
        self.assertFalse([c for c in self.commands if "--import" in c or "rm -rf" in c or "mktemp" in c])

    def test_malformed_query_is_fatal(self):
        for value in [None, [None], {STORE: {}}, [{"path": STORE, "narHash": 3}],
                      [{"path": STORE, "narHash": HASH}, {"path": STORE, "narHash": "different"}]]:
            with self.subTest(value=value):
                self.valid = value
                with self.assertRaises(RuntimeError):
                    self.backend.stage(self.closure)
        self.export.assert_not_called()

    def test_copy_failure_cleans_only_its_own_cache(self):
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"failed")):
            with self.assertRaisesRegex(RuntimeError, "cannot ship closure cache"):
                self.backend.stage(self.closure)
        self.assertEqual(self.commands[-1], "rm -rf -- " + PREFIX + "AAAAAAAA")
        self.backend._remote_nix.assert_called_once_with("path-info", "--all", "--json")
        self.backend._verify.assert_not_called()

    def test_import_failure_cleans_cache(self):
        self.import_error = "import failed"
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            with self.assertRaisesRegex(RuntimeError, "import failed"):
                self.backend.stage(self.closure)
        self.assertEqual(self.commands[-1], "rm -rf -- " + PREFIX + "AAAAAAAA")
        self.backend._verify.assert_not_called()

    def test_final_verify_error_is_fatal_after_private_cache_cleanup(self):
        self.backend._verify.side_effect = RuntimeError("final closure bytes differ")
        with patch("nixsci.deploy.ssh.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            with self.assertRaisesRegex(RuntimeError, "final closure bytes differ"):
                self.backend.stage(self.closure)
        self.assertEqual(self.commands[-1], "rm -rf -- " + PREFIX + "AAAAAAAA")

    def test_complete_verifier_remains_recursive_and_compares_hash_map(self):
        self.valid = {STORE: {"narHash": HASH}}
        SSH._verify(self.backend, self.closure)
        self.assertEqual(self.backend._remote_nix.call_args_list[0].args,
                         ("store", "verify", "--recursive", "--no-trust", STORE))
        self.assertEqual(self.backend._remote_nix.call_args_list[1].args,
                         ("path-info", "--recursive", "--json", STORE))
        self.valid[DEP] = {"narHash": HASH}
        with self.assertRaisesRegex(RuntimeError, "target closure NAR identity differs"):
            SSH._verify(self.backend, self.closure)

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


class ShipBootstrap(unittest.TestCase):
    """The static Nix is shipped only when the target's copy is missing or wrong, and replaced by rename.

    Overwriting it in place failed with 'Text file busy' while a job ran it, and cost 41 MB per backend
    object (one per job of a sweep) through the site gateway."""

    def setUp(self):
        self.backend = object.__new__(SSH)
        self.backend.__dict__.update(host="me@node", remote_bootstrap="/remote/bin/nix", nix="/local/nix",
                                     bootstrap_sha256="0" * 64, _scp=("scp", "-q"), _bootstrap_shipped=False)
        self.commands = []
        self.backend._remote = lambda command, **kw: (self.commands.append(command),
                                                       subprocess.CompletedProcess([], 0, b"", b""))[1]

    def test_a_verified_remote_copy_is_not_shipped_again(self):
        self.backend._check_bootstrap = Mock()          # verification passes: present, right hash
        with patch("nixsci.deploy.ssh.subprocess.run") as scp:
            self.backend._ship_bootstrap()
        scp.assert_not_called()
        self.assertTrue(self.backend._bootstrap_shipped)

    def test_a_missing_or_wrong_copy_is_shipped_to_a_temporary_name_and_renamed(self):
        calls = iter([RuntimeError("remote Nix bootstrap verification failed: []"), None])
        def check():
            outcome = next(calls)
            if outcome:
                raise outcome
        self.backend._check_bootstrap = Mock(side_effect=check)
        with patch("nixsci.deploy.ssh.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, b"", b"")) as scp:
            self.backend._ship_bootstrap()
        target = scp.call_args.args[0][-1]
        self.assertNotEqual(target, "me@node:/remote/bin/nix")      # never over the file a job may be running
        self.assertTrue(target.startswith("me@node:/remote/bin/nix."))
        rename = [c for c in self.commands if "mv -f" in c]
        self.assertEqual(len(rename), 1)
        self.assertIn("/remote/bin/nix", rename[0])
        self.assertTrue(self.backend._bootstrap_shipped)
