import unittest
import warnings
from unittest.mock import patch

from nixsci.deploy import nix
from nixsci.deploy.resolver import resolve


SYSTEM = "x86_64-linux"
ROOT = "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-deployment"
PROGRAM = ROOT + "/bin/run"
HASHES = {ROOT: "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
MANIFEST = {"format": "2.0", "program": PROGRAM}
SOURCE = {"flake": "fixture", "snapshot": ROOT}


class ResolveFallback(unittest.TestCase):
    def setup_resolve(self):
        patches = [
            patch("nixsci.deploy.resolver.nix.capture_source", return_value=(SOURCE, ROOT)),
            patch("nixsci.deploy.resolver.nix.verify", return_value=HASHES),
            patch("nixsci.deploy.resolver.nix.manifest", return_value=MANIFEST),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return patch("nixsci.deploy.resolver.nix.build")

    def test_missing_deployment_output_falls_back_with_warning(self):
        build = self.setup_resolve()
        missing = nix.NixError(
            "flake does not provide attribute 'packages.x86_64-linux.deployments.x86_64-linux.demo', "
            "'legacyPackages.x86_64-linux.deployments.x86_64-linux.demo' or 'deployments.x86_64-linux.demo'"
        )
        with build as mocked, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mocked.side_effect = [missing, ROOT]
            closure = resolve("fixture", "demo", SYSTEM)
        self.assertEqual(mocked.call_args_list[0].args[2], "deployments.x86_64-linux.demo")
        self.assertEqual(mocked.call_args_list[1].args[2], "experiments.x86_64-linux.demo")
        self.assertEqual(len(caught), 1)
        self.assertIn("legacy experiments", str(caught[0].message))
        self.assertEqual(closure.path, ROOT)
        self.assertEqual(closure.program, PROGRAM)

    def test_build_failure_propagates_unchanged_without_second_build(self):
        build = self.setup_resolve()
        failure = nix.NixError("builder failed: network unavailable")
        with build as mocked:
            mocked.side_effect = failure
            with self.assertRaises(nix.NixError) as caught:
                resolve("fixture", "demo", SYSTEM)
        self.assertIs(caught.exception, failure)
        mocked.assert_called_once()

    def test_qualified_reference_does_not_fallback(self):
        build = self.setup_resolve()
        failure = nix.NixError("flake does not provide attribute 'deployments.x86_64-linux.demo'")
        with build as mocked:
            mocked.side_effect = failure
            with self.assertRaises(nix.NixError) as caught:
                resolve("fixture", "deployments.x86_64-linux.demo", SYSTEM)
        self.assertIs(caught.exception, failure)
        mocked.assert_called_once_with("nix", ROOT, "deployments.x86_64-linux.demo", SYSTEM)

    def test_deployment_success_does_not_fallback(self):
        build = self.setup_resolve()
        with build as mocked:
            mocked.return_value = ROOT
            closure = resolve("fixture", "demo", SYSTEM)
        mocked.assert_called_once_with("nix", ROOT, "deployments.x86_64-linux.demo", SYSTEM)
        self.assertEqual(closure.path, ROOT)
        self.assertEqual(closure.metadata["format"], "2.0")


if __name__ == "__main__":
    unittest.main()
