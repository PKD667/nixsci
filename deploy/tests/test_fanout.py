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
        self.run_root = "/run"  # hosts of one job share a run_root: a static lease's hosts do

    def stage(self, closure):
        self.calls.append(("stage",))

    def enter(self, closure):
        return ["/bin/nix-" + self.name, "shell", closure.path, "--command"]

    def put_script(self, workdir, relpath, text):
        self.calls.append(("put_script", workdir, relpath, text))
        return f"{workdir}/{relpath}"

    @property
    def effects(self):
        """The calls that move the closure or start the job, without the agent writes."""
        return [c for c in self.calls if c[0] != "put_script"]

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
        group.stage_all(nodes, ["h0", "h1", "h2", "h3"], closure(), "RSH")
        self.assertEqual(nodes[0].calls, [("stage",)])
        for node, peer in zip(nodes[1:], ("h1", "h2", "h3")):
            self.assertEqual(node.calls, [("receive", "a", peer, "RSH")])

    def test_direct_staging_still_stages_every_host_from_the_controller(self):
        nodes = [Node("a"), Node("b")]
        group.launch(nodes, ["h0", "h1"], closure(), run_id="r1")
        self.assertEqual([n.effects[0] for n in nodes], [("stage",), ("stage",)])
        self.assertEqual(nodes[0].effects[1], ("launch", "r1"))

    def test_fanout_is_chosen_by_the_first_target_only(self):
        nodes = [Node("a", "fanout"), Node("b")]
        group.launch(nodes, ["h0", "h1"], closure(), run_id="r2", rsh="RSH")
        self.assertEqual(nodes[0].effects, [("stage",), ("launch", "r2")])
        self.assertEqual(nodes[1].effects, [("receive", "a", "h1", "RSH")])

    def test_stage_must_be_direct_or_fanout(self):
        with self.assertRaisesRegex(ValueError, "stage must be"):
            SSH(host="h", system="x86_64-linux", store="/s", run_root="/r",
                remote_bootstrap="/b/nix", rootless=False, stage="broadcast")


class Agent(unittest.TestCase):
    """PRRTE hands `plm_ssh_agent <path>` to every daemon it starts, and a daemon whose node has no executable
    at that path selects no plm component and dies with a NULL call (prted's node_regex_report, observed on
    Grid'5000 with PRRTE 4.1.0 and 3.0.12 alike). So the agent must exist at one path on every host."""

    def test_every_host_gets_the_same_agent_path_with_its_own_enter_prefix(self):
        nodes = [Node("a"), Node("b"), Node("c")]
        handle = group.launch(nodes, ["h0", "h1", "h2"], closure(), run_id="r3", rsh="RSH -x")
        for node in nodes:
            put = [c for c in node.calls if c[0] == "put_script"]
            self.assertEqual(len(put), 1, node.name)
            _, workdir, relpath, text = put[0]
            self.assertEqual((workdir, relpath), ("/run/r3", "inputs/agent"))
            self.assertIn('exec RSH -x "$host" /bin/nix-%s shell %s --command "$@"' % (node.name, STORE), text)
            self.assertTrue(text.startswith("#!/bin/sh\n"))
        launched = [c for c in nodes[0].calls if c[0] == "launch"]
        self.assertEqual(len(launched), 1)

    def test_the_agent_path_is_exported_to_the_job(self):
        seen = {}
        class First(Node):
            def launch(self, closure, **kwargs):
                seen.update(kwargs["env"])
                return {}
        nodes = [First("a"), Node("b")]
        group.launch(nodes, ["h0", "h1"], closure(), run_id="r4", rsh="RSH")
        self.assertEqual(seen["NIX_DEPLOY_AGENT"], "/run/r4/inputs/agent")

    def test_hosts_that_disagree_on_the_run_root_are_refused(self):
        nodes = [Node("a"), Node("b")]
        nodes[1].run_root = "/elsewhere"
        # a path that is the same on every host is the whole point of the agent
        with self.assertRaisesRegex(ValueError, "run_root"):
            group.launch(nodes, ["h0", "h1"], closure(), run_id="r5", rsh="RSH")


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

    def test_the_peers_store_root_is_made_0700_before_the_import(self):
        # nix creates the store's parent directory 0755 on its own. stage() refuses a store root that is
        # not owned mode 700, so a host first staged by fanout could never be staged directly afterwards
        # (observed on Grid'5000: store/ was 755 on every fanout peer, 700 on directly staged hosts).
        with patch("nixsci.deploy.nix.export_paths"), patch("nixsci.deploy.ssh.subprocess.run"):
            self.dest.receive(closure(), source=self.src, peer="peer1", rsh="RSH")
        transfer = self.src._remote.call_args_list[-1].args[0]
        self.assertIn("mkdir -m 700 -p /s/dst", transfer)
        self.assertLess(transfer.index("mkdir -m 700 -p /s/dst"), transfer.index("--import"))

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


class Wiring(unittest.TestCase):
    """`stage` set on a static provider reaches the backend the lab's span run builds."""

    def test_static_hosts_pass_stage_to_every_target_and_the_factory_builds_fanout(self):
        from nixsci.deploy import factory
        from nixsci.deploy.providers import Resources, Static

        lease = Static().acquire(Resources(hosts=2), {"hosts": "h0,h1", "user": "u", "stage": "fanout"})
        self.assertEqual([t["stage"] for t in lease.targets], ["fanout", "fanout"])
        backend = factory.backend("t0", {"t0": lease.targets[0]})
        self.assertEqual(backend.stage_mode, "fanout")

    def test_static_hosts_default_to_direct(self):
        from nixsci.deploy import factory
        from nixsci.deploy.providers import Resources, Static

        lease = Static().acquire(Resources(hosts=1), {"hosts": "h0", "user": "u"})
        self.assertEqual(factory.backend("t0", {"t0": lease.targets[0]}).stage_mode, "direct")

    def test_an_unknown_stage_is_refused_when_the_backend_is_built(self):
        from nixsci.deploy import factory
        from nixsci.deploy.providers import Resources, Static

        lease = Static().acquire(Resources(hosts=1), {"hosts": "h0", "user": "u", "stage": "broadcast"})
        with self.assertRaisesRegex(ValueError, "stage must be"):
            factory.backend("t0", {"t0": lease.targets[0]})


if __name__ == "__main__":
    unittest.main()
