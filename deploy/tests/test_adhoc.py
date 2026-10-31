"""Ad-hoc hosts: `--hosts a,b` (or NIXSCI_HOSTS) run an experiment on machines the user can already ssh to.

The ssh layer is faked: `ssh -G HOST` returns canned configuration, and `ssh -o BatchMode=yes HOST true`
returns a scripted status. The tests show which commands would run and in which order; they do not show
that a real host accepts them.
"""

import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from nixsci.deploy import adhoc
from nixsci.deploy.providers import Lease
from nixsci.lab import runner
from nixsci.lab import spec as spec_mod
from nixsci.deploy.model import Closure

SYSTEM = "x86_64-linux"
CLOSURE = SimpleNamespace(path="/nix/store/x-demo", source="{}", system=SYSTEM)


class FakeSSH:
    """Stands in for subprocess.run inside adhoc. `probe` maps a host to (status, stderr) for `true`."""

    def __init__(self, probe=None, users=None):
        self.calls, self.probe, self.users = [], probe or {}, users or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[1] == "-G":
            host = argv[2]
            text = f"user {self.users.get(host, 'alice')}\nhostname {host}.example\nport 22\n"
            return subprocess.CompletedProcess(argv, 0, text, "")
        status, stderr = self.probe.get(argv[3], (0, ""))
        return subprocess.CompletedProcess(argv, status, "", stderr)

    def probes(self):
        return [c[3] for c in self.calls if c[1:3] == ["-o", "BatchMode=yes"]]


def ssh_fake(**kw):
    fake = FakeSSH(**kw)
    return fake, mock.patch("nixsci.deploy.adhoc.subprocess.run", fake)


class ParseHosts(unittest.TestCase):
    def test_comma_list_is_split_and_trimmed(self):
        self.assertEqual(adhoc.parse_hosts(" a, b,,c "), ["a", "b", "c"])

    def test_empty_means_no_ad_hoc_hosts(self):
        self.assertEqual(adhoc.parse_hosts(""), [])

    def test_a_repeated_host_is_refused(self):
        with self.assertRaisesRegex(ValueError, "repeated"):
            adhoc.parse_hosts("a,b,a")

    def test_the_command_line_wins_over_the_environment(self):
        self.assertEqual(adhoc.hosts_from("x,y", {"NIXSCI_HOSTS": "a"}), ["x", "y"])
        self.assertEqual(adhoc.hosts_from(None, {"NIXSCI_HOSTS": "a,b"}), ["a", "b"])
        self.assertIsNone(adhoc.hosts_from(None, {}))


class SshConfig(unittest.TestCase):
    def test_user_is_read_from_ssh_g_and_host_is_the_alias(self):
        fake, patch = ssh_fake(users={"a": "carol"})
        with patch:
            cfg = adhoc.ssh_config("a")
        self.assertEqual(fake.calls, [["ssh", "-G", "a"]])
        self.assertEqual(cfg["user"], "carol")


class Preflight(unittest.TestCase):
    def test_the_first_host_is_probed_with_batch_mode(self):
        fake, patch = ssh_fake()
        with patch:
            adhoc.preflight("a")
        self.assertEqual(fake.calls, [["ssh", "-o", "BatchMode=yes", "a", "true"]])

    def test_a_refused_host_names_the_host_and_the_last_ssh_line(self):
        fake, patch = ssh_fake(probe={"a": (255, "Warning: x\nPermission denied (publickey).\n")})
        with patch, self.assertRaises(adhoc.AdhocError) as caught:
            adhoc.preflight("a")
        self.assertEqual(
            str(caught.exception),
            "cannot ssh to a without a password: Permission denied (publickey). Check ~/.ssh/config and ssh-agent.",
        )

    def test_silent_failure_reports_the_exit_status(self):
        fake, patch = ssh_fake(probe={"a": (255, "")})
        with patch, self.assertRaisesRegex(adhoc.AdhocError, "exit status 255"):
            adhoc.preflight("a")


class AdhocLease(unittest.TestCase):
    def spec_resources(self, **kw):
        return {"provider": "cluster", "system": SYSTEM, **kw}

    def test_two_hosts_give_one_static_target_each_with_a_workdir_from_ssh_g(self):
        fake, patch = ssh_fake()
        with patch:
            lease = adhoc.lease(["a", "b"], self.spec_resources(hosts=1))
        self.assertEqual(lease.hosts, ["a", "b"])
        self.assertEqual([t["host"] for t in lease.targets], ["a", "b"])
        t = lease.targets[0]
        self.assertEqual(t["backend"], "ssh")
        self.assertEqual(t["system"], SYSTEM)
        self.assertEqual(t["store"], "/tmp/alice-nix-deploy/store")
        self.assertEqual(t["run_root"], "/tmp/alice-nix-deploy/runs")
        self.assertEqual(t["remote_bootstrap"], "/tmp/alice-nix-deploy/bin/nix")

    def test_only_the_first_host_is_probed_and_the_probe_runs_before_ssh_g(self):
        fake, patch = ssh_fake()
        with patch:
            adhoc.lease(["a", "b"], self.spec_resources())
        self.assertEqual(fake.probes(), ["a"])
        self.assertEqual(fake.calls[0][1:3], ["-o", "BatchMode=yes"])

    def test_a_failed_preflight_stops_before_any_other_ssh_call(self):
        fake, patch = ssh_fake(probe={"a": (255, "Permission denied (publickey).")})
        with patch, self.assertRaisesRegex(adhoc.AdhocError, "cannot ssh to a without a password"):
            adhoc.lease(["a", "b"], self.spec_resources())
        self.assertEqual(len(fake.calls), 1)


class RunnerWithHosts(unittest.TestCase):
    """`runner.run(..., hosts=...)`: the provider is not consulted, and a multi-host job is one group launch."""

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

        def read_file(self, handle, path):
            return b'{"exit_code": 0}'

        def exists(self, handle, name):
            return False

        def stop(self, handle):
            pass

        def remove(self, handle):
            pass

    def spec(self, **extra):
        raw = {"kind": "experiment", "name": "adhoc-demo", "sweep": {"x": [1]},
               "resources": {"provider": "cluster"}, **extra}
        return spec_mod.from_json(raw)

    def run_on(self, spec, hosts):
        calls = []
        factory = SimpleNamespace(backend=lambda name, configs: self.Backend(name, calls))
        providers = mock.Mock()
        lease = adhoc_lease(hosts)

        def group_launch(backends, hosts_, closure, *, run_id, env):
            calls.append(("group", [b.name for b in backends], list(hosts_)))
            return {"run": run_id}

        out = Path(tempfile.mkdtemp())
        with mock.patch.object(runner, "_deploy", lambda: (factory, providers)), \
                mock.patch("nixsci.deploy.adhoc.lease", return_value=lease) as adhoc_mock, \
                mock.patch("nixsci.deploy.group.launch", group_launch):
            manifests = runner.run(spec, {SYSTEM: CLOSURE}, out, hosts=hosts, poll=0, log=lambda _: None)
        return calls, manifests, providers, adhoc_mock

    def test_the_provider_is_not_consulted_when_hosts_are_given(self):
        _, _, providers, adhoc_mock = self.run_on(self.spec(), ["a", "b"])
        providers.acquire.assert_not_called()
        adhoc_mock.assert_called_once()

    def test_two_hosts_without_a_span_in_the_spec_are_one_group_launch(self):
        calls, manifests, _, _ = self.run_on(self.spec(), ["a", "b"])
        self.assertEqual([c for c in calls if c[0] == "group"], [("group", ["t0", "t1"], ["a", "b"])])
        self.assertEqual(manifests[0]["hosts"], ["a", "b"])
        self.assertEqual(manifests[0]["span"], 2)

    def test_one_host_is_an_ordinary_single_host_job(self):
        calls, manifests, _, _ = self.run_on(self.spec(), ["a"])
        self.assertFalse([c for c in calls if c[0] == "group"])
        self.assertNotIn("span", manifests[0])

    def test_an_explicit_span_in_the_spec_is_kept(self):
        calls, _, _, _ = self.run_on(self.spec(span=1), ["a", "b"])
        self.assertFalse([c for c in calls if c[0] == "group"])


class AdhocCli(unittest.TestCase):
    def test_lab_exec_with_a_refused_host_stops_with_one_line_and_status_2(self):
        import contextlib
        import io
        import json
        from nixsci.lab import cli

        root = Path(tempfile.mkdtemp())
        spec = root / "spec.json"
        spec.write_text(json.dumps({"kind": "experiment", "name": "adhoc-cli", "sweep": {"x": [1]},
                                    "resources": {"provider": "cluster"}}))
        fake, patch = ssh_fake(probe={"a": (255, "Permission denied (publickey).")})
        err = io.StringIO()
        with patch, mock.patch("nixsci.deploy.resolver.from_path", return_value=CLOSURE), \
                contextlib.redirect_stderr(err):
            code = cli.main(["--project", str(root), "exec", "--spec", str(spec), "--hosts", "a,b",
                             "--closure", f"{SYSTEM}=/nix/store/x-demo", "--source", '{"rev": "abc"}'])
        self.assertEqual(code, 2)
        self.assertIn("cannot ssh to a without a password: Permission denied (publickey).", err.getvalue())
        self.assertEqual(fake.probes(), ["a"])


def adhoc_lease(hosts):
    return Lease([{"backend": "ssh", "system": SYSTEM, "host": h, "store": "/s", "run_root": "/r",
                   "remote_bootstrap": "/b", "rootless": True} for h in hosts], list(hosts))


if __name__ == "__main__":
    unittest.main()
