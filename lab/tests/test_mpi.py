"""nixsci.lab.mpi: the mpirun command line for the hosts of this job, so no project scripts it.

Each rule below was learned on a real cluster: PRRTE 4 ignores the Open MPI 4 name of the launch agent without a
warning, and forwards the agent's path to its daemons, which die on any host that lacks it."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nixsci.lab import mpi


class Command(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {"NIX_DEPLOY_WORKDIR": self.dir.name}
        self.version = patch.object(mpi, "_mpirun_version", return_value="mpirun (Open MPI) 5.0.11")
        self.version.start()
        self.addCleanup(self.version.stop)

    def hostfile(self, hosts):
        path = Path(self.dir.name) / "hosts"
        path.write_text("".join(h + "\n" for h in hosts))
        self.env["NIX_DEPLOY_HOSTFILE"] = str(path)
        self.env["NIX_DEPLOY_AGENT"] = "/run/x/inputs/agent"

    def cmd(self, *a, **kw):
        with patch.dict(os.environ, self.env, clear=True):
            return mpi.command(*a, **kw)

    def test_one_host_is_plain_mpirun(self):
        self.assertEqual(self.cmd(4), ["mpirun", "--oversubscribe", "-n", "4", "--bind-to", "none"])

    def test_several_hosts_get_a_hostfile_a_slot_map_and_the_agent(self):
        self.hostfile(["a", "b"])
        c = self.cmd(4)
        self.assertEqual(c[:6], ["mpirun", "--oversubscribe", "-n", "4", "--bind-to", "none"])
        self.assertIn("--hostfile", c)
        self.assertEqual(c[c.index("--map-by") + 1], "slot")
        self.assertEqual(Path(c[c.index("--hostfile") + 1]).read_text(), "a slots=2\nb slots=2\n")
        i = c.index("--prtemca")
        self.assertEqual(c[i:i + 3], ["--prtemca", "plm_ssh_agent", "/run/x/inputs/agent"])

    def test_extra_ranks_go_on_the_last_host(self):
        self.hostfile(["a", "b"])
        c = self.cmd(6, extra=1)  # extra = more slots (not ranks) on the last host: room for a coordinator
        self.assertEqual(Path(c[c.index("--hostfile") + 1]).read_text(), "a slots=3\nb slots=4\n")

    def test_per_node_overrides_the_even_split(self):
        self.hostfile(["a", "b", "c"])
        c = self.cmd(5, per_node=8)
        self.assertEqual(Path(c[c.index("--hostfile") + 1]).read_text(), "a slots=8\nb slots=8\nc slots=8\n")

    def test_open_mpi_4_uses_its_own_name_for_the_launch_agent(self):
        self.hostfile(["a", "b"])
        with patch.object(mpi, "_mpirun_version", return_value="mpirun (Open MPI) 4.1.6"):
            c = self.cmd(2)
        i = c.index("--mca")
        self.assertEqual(c[i:i + 3], ["--mca", "plm_rsh_agent", "/run/x/inputs/agent"])
        self.assertNotIn("--prtemca", c)

    def test_a_missing_agent_is_a_plain_error_not_a_crashed_daemon(self):
        self.hostfile(["a", "b"])
        del self.env["NIX_DEPLOY_AGENT"]
        with self.assertRaisesRegex(RuntimeError, "NIX_DEPLOY_AGENT"):
            self.cmd(2)

    def test_exported_variables_and_bind_policy(self):
        c = self.cmd(2, bind="core", export=["FOO", "BAR"])
        self.assertEqual(c[c.index("--bind-to") + 1], "core")
        self.assertEqual([c[i + 1] for i, a in enumerate(c) if a == "-x"], ["FOO", "BAR"])

    def test_hosts_lists_the_job_hosts(self):
        with patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(mpi.hosts(), [])
        self.hostfile(["a", "b"])
        with patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(mpi.hosts(), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
