"""Fanout staging: the controller copies to the first host once; the other hosts copy from it.

These tests use fake backends and a mocked ssh layer. They show which host each copy is
issued from and that the controller's export and scp are not used; they do not show that
a real allocation can reach its peers.
"""
import base64
import subprocess
import unittest
from unittest.mock import Mock, patch

from nixsci.deploy import group
from nixsci.deploy.model import Closure
from nixsci.deploy.ssh import SSH

STORE = "/nix/store/" + "a" * 32 + "-experiment"
DEP = "/nix/store/" + "b" * 32 + "-dependency"
HASH = "sha256-" + base64.b64encode(bytes(32)).decode()


class Node:
    def __init__(self, name, mode="direct"):
        self.name, self.stage_mode, self.calls = name, mode, []
        self.run_root = "/run/" + name

    def stage(self, closure):
        self.calls.append(("stage",))

    def receive(self, closure, *, source, peer, rsh):
        self.calls.append(("receive", source.name, peer, rsh))

    def launch(self, closure, **kwargs):
        self.calls.append(("launch", kwargs["run_id"]))
        return {}


def closure():
    return Closure(STORE, STORE + "/bin/run", {}, {STORE: HASH}, "committed-source", "x86_64-linux")


class Stage(unittest.TestCase):
    def test_the_controller_stages_the_first_host_once_and_the_others_copy_from_it(self):
        nodes = [Node(n) for n in ("a", "b", "c", "d")]
        group.fanout.stage_all(nodes, ["h0", "h1", "h2", "h3"], closure(), "RSH")
        self.assertEqual(nodes[0].calls, [("stage",)])
        for node, peer in zip(nodes[1:], ("h1", "h2", "h3")):
            self.assertEqual(node.calls, [("receive", "a", peer, "RSH")])

    def test_direct_staging_still_stages_every_host_from_the_controller(self):
        nodes = [Node("a"), Node("b")]
        group.launch(nodes, ["h0", "h1"], closure(), run_id="r1")
        self.assertEqual([n.calls[0] for n in nodes], [("stage",), ("stage",)])
        self.assertEqual(nodes[0].calls[1], ("launch", "r1"))

    def test_fanout_is_chosen_by_the_first_target_only(self):
        nodes = [Node("a", "fanout"), Node("b")]
        group.launch(nodes, ["h0", "h1"], closure(), run_id="r2", rsh="RSH")
        self.assertEqual(nodes[0].calls, [("stage",), ("launch", "r2")])
        self.assertEqual(nodes[1].calls, [("receive", "a", "h1", "RSH")])

    def test_stage_must_be_direct_or_fanout(self):
        with self.assertRaisesRegex(ValueError, "stage must be"):
            SSH(host="h", system="x86_64-linux", store="/s", run_root="/r",
                remote_bootstrap="/b/nix", rootless=False, stage="broadcast")


class Receive(unittest.TestCase):
    def setUp(self):
        self.dest = object.__new__(SSH)
        self.dest.__dict__.update(host="peer1", system="x86_64-linux", store="/s/dst",
                                  run_root="/run/dst", remote_bootstrap="/run/dst/bin/nix",
                                  bootstrap_sha256="0" * 64, _bootstrap_shipped=False)
        self.dest._admit, self.dest._wait_ready = Mock(), Mock()
        self.dest._check_bootstrap, self.dest._verify = Mock(), Mock()
        self.dest._remote_hashes = Mock(return_value={})
        self.dest._remote = Mock()
        self.src = object.__new__(SSH)
        self.src.__dict__.update(host="first", system="x86_64-linux", store="/s/src",
                                 run_root="/run/src", remote_bootstrap="/run/src/bin/nix")
        self.src._remote = Mock(return_value=subprocess.CompletedProcess([], 0, b"", b""))

    def test_missing_paths_come_from_the_first_host_through_a_pipe(self):
        with patch("nixsci.deploy.nix.export_paths") as controller_export, \
                patch("nixsci.deploy.ssh.subprocess.run") as controller_scp:
            result = self.dest.receive(closure(), source=self.src, peer="peer1", rsh="RSH")
        controller_export.assert_not_called()
        controller_scp.assert_not_called()
        self.assertEqual(self.dest._remote.call_count, 0)  # the peer's commands run inside the pipe
        bootstrap, transfer = (c.args[0] for c in self.src._remote.call_args_list)
        self.assertIn("cat /run/src/bin/nix | RSH peer1 ", bootstrap)
        self.assertIn("--export", transfer)
        self.assertIn("| RSH peer1 '", transfer)
        self.assertIn("--import", transfer)
        self.assertIn("rm -rf -- \"$d\"", transfer)
        self.assertIn(STORE, transfer)
        self.dest._verify.assert_called_once()
        self.assertEqual(result["path"], STORE)

    def test_paths_the_peer_already_has_are_not_sent(self):
        self.dest._remote_hashes = Mock(return_value={STORE: HASH})
        self.dest._bootstrap_shipped = True
        self.dest.receive(closure(), source=self.src, peer="peer1", rsh="RSH")
        self.src._remote.assert_not_called()
        self.dest._verify.assert_called_once()

    def test_a_peer_with_a_different_nar_identity_is_refused(self):
        self.dest._remote_hashes = Mock(return_value={STORE: "sha256-other"})
        with self.assertRaisesRegex(RuntimeError, "NAR identity differs"):
            self.dest.receive(closure(), source=self.src, peer="peer1", rsh="RSH")
        # Only the bootstrap moved: the peer's path-info needs it, the closure stream did not start.
        self.assertEqual(self.src._remote.call_count, 1)
        self.assertNotIn("--export", self.src._remote.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
