"""Paths that cache.nixos.org has are fetched by the target itself; the rest are exported.

The controller decides which paths are on the cache (one narinfo request per path). The target runs
`nix copy --from https://cache.nixos.org` with signatures checked. A path the target did not register
is streamed from the controller as before, and one line says which path and why.

Fakes only: nothing here contacts a network or a remote host. A real run against cache.nixos.org is
a separate check, not part of this file.
"""
import base64
import io
import json
import shlex
import subprocess
import tempfile
import unittest
import unittest.mock
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from nixsci.deploy import nix
from nixsci.deploy.model import Closure
from nixsci.deploy.ssh import SSH

STORE = "/nix/store/" + "a" * 32 + "-experiment"
DEP = "/nix/store/" + "b" * 32 + "-dependency"
HASH = "sha256-" + base64.b64encode(bytes(32)).decode()
PREFIX = "/local/me/nixsci/runs/.stage/nar-" + "a" * 32 + "-"
CACHE_KEY = "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="


class NarinfoProbe(unittest.TestCase):
    def test_present_path_is_cached_absent_path_is_local(self):
        with patch("nixsci.deploy.nix.urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value.status = 200
            self.assertTrue(nix.narinfo_exists(DEP))
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, nix.CACHE_URL + "/" + "b" * 32 + ".narinfo")
            self.assertEqual(request.get_method(), "HEAD")
        error = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch("nixsci.deploy.nix.urllib.request.urlopen", side_effect=error):
            self.assertFalse(nix.narinfo_exists(DEP))

    def test_unreachable_cache_is_an_error_not_a_miss(self):
        with patch("nixsci.deploy.nix.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timed out")):
            with self.assertRaisesRegex(nix.CacheUnreachable, "timed out"):
                nix.narinfo_exists(DEP)
        error = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
        with patch("nixsci.deploy.nix.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(nix.CacheUnreachable, "503"):
                nix.narinfo_exists(DEP)

    def test_name_without_a_store_hash_is_never_on_the_cache(self):
        with patch("nixsci.deploy.nix.urllib.request.urlopen") as opened:
            self.assertFalse(nix.narinfo_exists("/nix/store/not-a-hash"))
        opened.assert_not_called()


class Partition(unittest.TestCase):
    def test_splits_in_closure_order(self):
        cached, local, reason = nix.cache_partition([STORE, DEP], has=lambda p: p == DEP)
        self.assertEqual((cached, local, reason), ([DEP], [STORE], None))

    def test_unreachable_cache_makes_every_path_local_with_a_reason(self):
        def has(path):
            raise nix.CacheUnreachable("timed out")
        cached, local, reason = nix.cache_partition([STORE, DEP], has=has)
        self.assertEqual((cached, local), ([], [STORE, DEP]))
        self.assertIn("timed out", reason)


class FetchCommand(unittest.TestCase):
    def setUp(self):
        self.backend = object.__new__(SSH)
        self.backend.__dict__.update(host="me@node", store="/local/me/store",
                                     remote_bootstrap="/remote/bin/nix", nix="/local/nix")
        self.commands = []
        self.backend._remote = Mock(side_effect=self._record)
        self.backend._remote_hashes = Mock(return_value={DEP: HASH})

    def _record(self, command, **kwargs):
        self.commands.append(command)
        return subprocess.CompletedProcess([], 0, b"", b"")

    def test_target_copies_from_cache_with_signatures_checked(self):
        self.backend._fetch_from_cache([DEP])
        command = self.commands[0]
        words = shlex.split(command)
        self.assertEqual(words[0], "/remote/bin/nix")
        self.assertIn("copy", words)
        self.assertEqual(words[words.index("--from") + 1], "https://cache.nixos.org")
        self.assertEqual(words[words.index("--option") + 1], "trusted-public-keys")
        self.assertEqual(words[words.index("--option") + 2], CACHE_KEY)
        self.assertNotIn("--no-check-sigs", words)
        self.assertEqual(words[-1], DEP)
        self.assertNotIn(STORE, command)


class StageWithCache(unittest.TestCase):
    """stage() on a fake target. `self.registered` is the target's store; a copy from the cache may change it."""

    def setUp(self):
        self.closure = Closure(STORE, STORE + "/bin/run", {}, {STORE: HASH, DEP: HASH},
                               "committed-source", "x86_64-linux")
        self.backend = object.__new__(SSH)
        self.backend.__dict__.update(host="me@node", run_root="/local/me/nixsci/runs",
                                     store="/local/me/nixsci/store", nix="/pinned/nix",
                                     remote_bootstrap="/remote/bin/nix", _scp=("scp", "-q"))
        self.backend._admit = Mock()
        self.backend._wait_ready = Mock()
        self.backend._ship_bootstrap = Mock()
        self.backend._verify = Mock()
        self.registered = {}            # path -> narHash on the target
        self.copy_result = None         # (returncode, stderr) of the cache copy; None = success
        self.copy_registers = {DEP: HASH}   # what the cache copy leaves registered on the target
        self.cache_has = {DEP}          # what the controller's partition sees on the cache
        self.commands = []
        self.backend._remote_nix = Mock(side_effect=self._remote_nix)
        self.backend._remote = Mock(side_effect=self._remote)
        self.export = patch("nixsci.deploy.ssh.nix.export_paths").start()
        self.addCleanup(patch.stopall)
        patch("nixsci.deploy.ssh.subprocess.run",
              return_value=subprocess.CompletedProcess([], 0, b"", b"")).start()
        patch("nixsci.deploy.nix.cache_partition", side_effect=self._partition).start()
        self.partition_reason = None

    def _partition(self, paths, has=None):
        if self.partition_reason:
            return [], list(paths), self.partition_reason
        return [p for p in paths if p in self.cache_has], [p for p in paths if p not in self.cache_has], None

    def _remote_nix(self, *args):
        if args[0] == "path-info":
            entries = [{"path": p, "narHash": h} for p, h in self.registered.items()]
            return subprocess.CompletedProcess([], 0, json.dumps(entries).encode(), b"")
        return subprocess.CompletedProcess([], 0, b"", b"")

    def _remote(self, command, **kwargs):
        self.commands.append(command)
        if "copy" in shlex.split(command) and "--from" in shlex.split(command):
            self.registered.update(self.copy_registers)
            code, stderr = self.copy_result or (0, "")
            return subprocess.CompletedProcess([], code, b"", stderr.encode())
        if command == "id -u":
            return subprocess.CompletedProcess([], 0, b"1000\n", b"")
        if command.startswith("realpath -m --"):
            return subprocess.CompletedProcess([], 0, (shlex.split(command)[-1] + "\n").encode(), b"")
        if command.startswith("umask 077; mkdir -m 700") or "stat -c" in command:
            return subprocess.CompletedProcess([], 0, b"1000 700\n1000\n", b"")
        if "mktemp" in command:
            return subprocess.CompletedProcess([], 0, (PREFIX + "AAAAAAAA\n").encode(), b"")
        return subprocess.CompletedProcess([], 0, b"", b"")

    def copy_commands(self):
        return [c for c in self.commands if "--from" in shlex.split(c)]

    def test_cache_path_is_fetched_by_target_and_only_the_rest_is_exported(self):
        self.registered = {}
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.backend.stage(self.closure)
        self.assertEqual(len(self.copy_commands()), 1)
        self.assertIn(DEP, self.copy_commands()[0])
        self.assertNotIn(STORE, self.copy_commands()[0])
        self.assertEqual(self.export.call_args.args[2:], (STORE,))
        self.assertEqual(err.getvalue(), "")
        self.backend._verify.assert_called_once_with(self.closure)

    def test_failed_fetch_falls_back_to_export_with_one_line_saying_which_and_why(self):
        self.copy_registers = {}
        self.copy_result = (1, "warning: something\nerror: unable to download "
                               "'https://cache.nixos.org/x.narinfo': Connection refused\n")
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.backend.stage(self.closure)
        self.assertEqual(self.export.call_args.args[2:], (STORE, DEP))
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(DEP, lines[0])
        self.assertNotIn(STORE, lines[0])          # project path: never a fallback, not named
        self.assertIn("Connection refused", lines[0])
        self.backend._verify.assert_called_once_with(self.closure)

    def test_failed_fetch_that_registered_one_path_exports_only_the_other(self):
        other = DEP + "x"
        self.closure = Closure(STORE, STORE + "/bin/run", {}, {STORE: HASH, DEP: HASH, other: HASH},
                               "src", "x86_64-linux")
        self.cache_has = {DEP, other}
        self.copy_registers = {DEP: HASH}
        self.copy_result = (1, "error: unable to download 'https://cache.nixos.org/y.narinfo'\n")
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.backend.stage(self.closure)
        self.assertEqual(self.export.call_args.args[2:], (STORE, other))
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(other, lines[0])
        self.assertNotIn(DEP + " ", lines[0])

    def test_path_the_fetch_registers_with_wrong_identity_is_fatal(self):
        self.copy_registers = {DEP: "sha256-wrong"}
        with self.assertRaisesRegex(RuntimeError, "cache-fetched target path NAR identity differs"):
            self.backend.stage(self.closure)
        self.export.assert_not_called()
        self.backend._verify.assert_not_called()

    def test_nothing_missing_means_no_cache_query(self):
        self.registered = {STORE: HASH, DEP: HASH}
        with patch("nixsci.deploy.nix.cache_partition") as partition:
            self.backend.stage(self.closure)
        partition.assert_not_called()
        self.assertEqual(self.copy_commands(), [])

    def test_everything_local_means_no_fetch_and_no_line(self):
        self.cache_has = set()
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.backend.stage(self.closure)
        self.assertEqual(self.copy_commands(), [])
        self.assertEqual(self.export.call_args.args[2:], (STORE, DEP))
        self.assertEqual(err.getvalue(), "")

    def test_controller_cannot_reach_cache_exports_everything_with_one_line(self):
        self.partition_reason = "controller cannot reach cache.nixos.org: timed out"
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            self.backend.stage(self.closure)
        self.assertEqual(self.copy_commands(), [])
        self.assertEqual(self.export.call_args.args[2:], (STORE, DEP))
        self.assertEqual(self.backend._remote_nix.call_args_list.count(
            unittest.mock.call("path-info", "--all", "--json")), 1)   # no second query on the target
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("timed out", lines[0])


if __name__ == "__main__":
    unittest.main()


class ParallelLookup(unittest.TestCase):
    """66 sequential HEADs are 66 round trips on a slow uplink: the lookups overlap, and the answer keeps closure order."""

    def test_lookups_overlap_and_order_is_kept(self):
        import threading, time
        from nixsci.deploy import nix
        active, peak, lock = 0, 0, threading.Lock()

        def has(path):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return path.endswith("a")

        paths = [f"/nix/store/{i:032d}-p{'a' if i % 2 else 'b'}" for i in range(40)]
        on_cache, local, reason = nix.cache_partition(paths, has=has)
        self.assertGreater(peak, 1)
        self.assertEqual(on_cache, [p for p in paths if p.endswith("a")])
        self.assertEqual(local, [p for p in paths if p.endswith("b")])
        self.assertIsNone(reason)

    def test_one_unreachable_answer_sends_everything_to_the_export(self):
        from nixsci.deploy import nix

        def has(path):
            if path.endswith("3"):
                raise nix.CacheUnreachable("timed out")
            return True

        paths = [f"/nix/store/{i:032d}-p{i}" for i in range(8)]
        on_cache, local, reason = nix.cache_partition(paths, has=has)
        self.assertEqual((on_cache, local), ([], paths))
        self.assertIn("timed out", reason)
