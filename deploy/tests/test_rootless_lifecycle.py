"""Regression checks for asynchronous proof publication."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "rootless_lifecycle", Path(__file__).with_name("rootless-lifecycle.py")
)
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class Backend:
    def __init__(self, exists):
        self.observations = iter(exists)

    def exists(self, handle, path):
        return next(self.observations)

    def alive(self, handle):
        return False

    def read_file(self, handle, path):
        return '{"status": "pass"}' if path == "rootless-proof.json" else "failed"


class PublicationTests(unittest.TestCase):
    def test_publish_and_exit_between_observations(self):
        self.assertEqual(
            lifecycle.wait_for_proof(Backend([False, True]), {}, 1),
            {"status": "pass"},
        )

    def test_exit_without_proof_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, "exited before publishing"):
            lifecycle.wait_for_proof(Backend([False, False]), {}, 1)
