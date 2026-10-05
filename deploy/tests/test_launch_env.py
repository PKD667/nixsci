import base64
import unittest

from nixsci.deploy.backend import Backend
from nixsci.deploy.model import Closure

STORE = "/nix/store/" + "a" * 32 + "-exp"
ZERO = "sha256-" + base64.b64encode(bytes(32)).decode()


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


class LaunchEnvironment(unittest.TestCase):
    def launch(self, **env):
        backend = Capture()
        closure = Closure(STORE, STORE + "/bin/run", {}, {STORE: ZERO}, "src", "x86_64-linux")
        backend.launch(closure, run_id="r1", env=env)
        return backend.env

    def test_a_job_never_writes_bytecode_into_the_immutable_store(self):
        self.assertEqual(self.launch()["PYTHONDONTWRITEBYTECODE"], "1")

    def test_the_caller_can_still_decide(self):
        self.assertEqual(self.launch(PYTHONDONTWRITEBYTECODE="")["PYTHONDONTWRITEBYTECODE"], "")


if __name__ == "__main__":
    unittest.main()
