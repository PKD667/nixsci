import unittest
from unittest import mock

from nix_deploy import providers


class OarProvider(unittest.TestCase):
    def acquire(self, outputs):
        calls = []

        def fake(argv, command, check=True):
            calls.append(command)
            return outputs.pop(0) if command.startswith(("oarstat", "oarsub")) else ""

        opts = {
            "login": "u",
            "site": "s",
            "bootstrap": "/b",
            "bootstrap_sha256": "0" * 64,
            "poll": 0,
        }
        with mock.patch.object(providers, "_ssh", fake):
            lease = providers.OAR().acquire(providers.Resources(hosts=2), opts)
        return lease, calls

    def test_lease_state_survives_waiting_and_release_deletes_the_job(self):
        lease, calls = self.acquire(
            [
                "OAR_JOB_ID=42\n",
                "  state = Waiting\n",
                "  state = Running\n  assigned_hostnames = a+b+a\n",
            ]
        )
        self.assertEqual(lease.state["job"], "42")
        self.assertEqual(lease.hosts, ["a", "b"])
        with mock.patch.object(
            providers, "_ssh", lambda argv, cmd, check=True: calls.append(cmd) or ""
        ):
            lease.release()
        self.assertEqual(calls[-1], "oardel 42")

    def test_unknown_and_misplaced_options_are_refused(self):
        with self.assertRaises(ValueError):
            providers.Resources.of({"hosts": 1, "site": "lille"})
        with self.assertRaises(ValueError):
            providers.Local().acquire(providers.Resources(), {"site": "lille"})


if __name__ == "__main__":
    unittest.main()
