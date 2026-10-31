"""Real Nix regression: a partial graph cannot use an empty binary cache.

Uses the nix on PATH (NIXSCI_TEST_NIX overrides it). Always run; there is no opt-in.
The replacement native export must import when references exist on the target.
"""
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

from nixsci.deploy import nix as api


class PartialGraphIntegration(unittest.TestCase):
    def test_partial_native_export_preserves_complete_identity(self):
        nix = os.environ.get("NIXSCI_TEST_NIX") or shutil.which("nix")
        self.assertTrue(nix, "no nix on PATH")
        expression = '''let d = builtins.toFile "nixsci-export-dep" "verified dependency";
          r = builtins.toFile "nixsci-export-root" "depends on ${d}\\n";
          in { dep = d; root = r; }'''
        pair = json.loads(api.run(nix, None, "eval", "--json", "--expr", expression).stdout)
        expected = api.path_hashes(nix, None, pair["root"])
        self.assertEqual(set(expected), {pair["dep"], pair["root"]})
        with tempfile.TemporaryDirectory(prefix="nixsci-real-export-") as tmp:
            tmp = Path(tmp)
            store = f"local?root={tmp / 'target'}"
            def legacy(*args, stdin=None):
                return subprocess.run(["nix-store", "--store", store, *args],
                                      executable=nix, stdin=stdin,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            init = legacy("--init")
            self.assertEqual(init.returncode, 0, init.stderr.decode())
            dep = tmp / "dep.export"
            api.export_paths(nix, dep, pair["dep"])
            with dep.open("rb") as stream:
                imported = legacy("--import", stdin=stream)
            self.assertEqual(imported.returncode, 0, imported.stderr.decode())
            # Reproduce the failed optimization with REAL Nix, not a mock.
            with self.assertRaisesRegex(api.NixError, "reference .* is not valid"):
                api.copy_to_cache(nix, str(tmp / "bad-cache"), pair["root"], recursive=False)
            root = tmp / "root.export"
            api.export_paths(nix, root, pair["root"])
            self.assertLess(root.stat().st_size, 65536)
            with root.open("rb") as stream:
                imported = legacy("--import", stdin=stream)
            self.assertEqual(imported.returncode, 0, imported.stderr.decode())
            self.assertEqual(api.verify(nix, store, pair["root"]), expected)


if __name__ == "__main__":
    unittest.main()
