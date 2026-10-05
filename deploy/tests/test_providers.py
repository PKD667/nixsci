import hashlib
import os
import stat
import tempfile
import unittest
import unittest.mock

from nixsci.deploy import group, providers
from nixsci.deploy.ssh import SSH


def bootstrap():
    path = os.path.join(tempfile.mkdtemp(), "nix")
    with open(path, "wb") as f:
        f.write(b"#!/bin/sh\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path, hashlib.sha256(open(path, "rb").read()).hexdigest()


class Options(unittest.TestCase):
    def test_provider_settings_outside_opts_are_refused(self):
        with self.assertRaises(ValueError):
            providers.Resources.of({"hosts": 1, "site": "lille"})
        with self.assertRaises(ValueError):
            providers.Local().acquire(providers.Resources(), {"site": "lille"})

    def test_there_is_no_scheduler_in_nixsci_deploy(self):
        self.assertEqual(sorted(providers._BUILTIN), ["local", "static"])


class StaticHosts(unittest.TestCase):
    def opts(self, **extra):
        path, digest = bootstrap()
        return {
            "hosts": "a, b",
            "workdir": "/tmp/me-nd/",
            "bootstrap": path,
            "bootstrap_sha256": digest,
            **extra,
        }

    def test_a_host_list_becomes_ssh_targets_that_share_a_template(self):
        lease = providers.Static().acquire(
            providers.Resources(hosts=2),
            self.opts(user="me", jump="me@gw", ssh_command=["oarsh"], ready_timeout=60),
        )
        self.assertEqual(lease.hosts, ["a", "b"])
        first = lease.targets[0]
        self.assertEqual(first["host"], "me@a")
        self.assertEqual(first["store"], "/tmp/me-nd/store")
        self.assertEqual(first["run_root"], "/tmp/me-nd/runs")
        self.assertEqual(first["remote_bootstrap"], "/tmp/me-nd/bin/nix")
        self.assertEqual(
            (first["jump"], first["ssh_command"], first["ready_timeout"]), ("me@gw", ["oarsh"], 60)
        )

    def test_wrong_requests_are_refused(self):
        with self.assertRaises(ValueError):
            providers.Static().acquire(providers.Resources(hosts=3), self.opts())
        with self.assertRaises(ValueError):
            providers.Static().acquire(providers.Resources(), self.opts(workdir="relative"))
        with self.assertRaises(ValueError):
            providers.Static().acquire(providers.Resources(), self.opts(queue="besteffort"))


class SshLayer(unittest.TestCase):
    def backend(self, **extra):
        path, digest = bootstrap()
        return SSH(
            host="me@a",
            system="x86_64-linux",
            store="/s",
            run_root="/r",
            bootstrap=path,
            bootstrap_sha256=digest,
            remote_bootstrap="/b/nix",
            **extra,
        )

    def test_command_options_and_jump_are_configurable(self):
        b = self.backend(
            ssh_command=("oarsh",),
            scp_command=("scp", "-q", "-S", "oarsh"),
            jump="me@gw",
            ssh_options=("-o", "StrictHostKeyChecking=accept-new"),
        )
        self.assertEqual(b._ssh[0], "oarsh")
        self.assertIn("ProxyJump=me@gw", b._ssh)
        self.assertIn("StrictHostKeyChecking=accept-new", b._scp)
        self.assertEqual(b._scp[:4], ("scp", "-q", "-S", "oarsh"))

    def test_defaults_are_plain_ssh(self):
        b = self.backend()
        self.assertEqual((b._ssh[0], b._scp[0]), ("ssh", "scp"))
        self.assertNotIn("ProxyJump", " ".join(b._ssh))

    def test_a_malformed_jump_is_refused(self):
        with self.assertRaises(ValueError):
            self.backend(jump="gw; rm -rf /")

    def test_hosts_reach_each_other_the_way_the_target_says(self):
        b = self.backend(rsh="oarsh")
        self.assertEqual(b.rsh, "oarsh")


class BootstrapIsAFlakeReference(unittest.TestCase):
    def fake_nix(self):
        path, digest = bootstrap()
        calls = []

        def resolve(ref, nix="nix"):
            calls.append(ref)
            return path

        return path, digest, calls, resolve

    def test_a_rootless_target_needs_no_binary_or_hash_in_its_config(self):
        path, digest, calls, resolve = self.fake_nix()
        with unittest.mock.patch("nixsci.deploy.backend.resolve_bootstrap", resolve):
            b = SSH(
                host="me@a",
                system="x86_64-linux",
                store="/s",
                run_root="/r",
                remote_bootstrap="/b/nix",
            )
        self.assertEqual(calls, ["nixpkgs#nixStatic"])
        self.assertEqual((b.nix, b.bootstrap_sha256), (path, digest))

    def test_a_wrong_pinned_hash_for_a_reference_is_refused(self):
        _, _, _, resolve = self.fake_nix()
        with unittest.mock.patch("nixsci.deploy.backend.resolve_bootstrap", resolve):
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

    def test_static_hosts_need_only_hosts_and_a_user(self):
        lease = providers.Static().acquire(providers.Resources(), {"hosts": "a", "user": "me"})
        t = lease.targets[0]
        self.assertEqual((t["host"], t["store"]), ("me@a", "/tmp/me-nix-deploy/store"))
        self.assertNotIn("bootstrap", t)
        with self.assertRaises(ValueError):
            providers.Static().acquire(providers.Resources(), {"hosts": "a"})


if __name__ == "__main__":
    unittest.main()
