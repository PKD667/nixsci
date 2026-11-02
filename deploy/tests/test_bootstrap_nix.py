"""The rootless bootstrap is the Nix project's own static Nix, patched, and a cold start compiles one derivation.

History: the bootstrap was nixpkgs' `pkgsStatic.nix` with two patches. No cache holds that derivation, so the first
deploy from a clean store compiled all of Nix (about 40 minutes, 0.5 to 0.8 GB per compiler job; on a 15 GB machine
one kernel OOM kill), and an `assert` on the caller's nixpkgs version made every project whose nixpkgs moved on
fail to evaluate. The Nix project's Hydra builds `packages.<system>.nix-cli-static` and uploads every component
(measured: 185 paths, 206 MiB fetched, 1 derivation built, for the patched nix-cli). These tests keep it so.
They need a real nix and the network, so the sandboxed flake checks leave them out (flake.nix, needsNix)."""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAKE = f'builtins.getFlake "{ROOT}"'
STUB = '{ stdenv.hostPlatform.system = "x86_64-linux"; }'  # a pkgs that has no pkgsStatic, and no nix of any version


def nix(*args, expr=None):
    cmd = ["nix", "--extra-experimental-features", "nix-command flakes", *args]
    if expr is not None:
        cmd += ["--impure", "--expr", expr]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


class Bootstrap(unittest.TestCase):
    def test_it_does_not_look_at_the_callers_nixpkgs(self):
        got = nix("eval", "--raw", expr=f"(({FLAKE}).lib.rootlessBootstrap {{ pkgs = {STUB}; }}).version")
        self.assertEqual((got.returncode, got.stdout), (0, "2.34.8"), got.stderr[-600:])

    def test_it_carries_the_namespace_root_patch(self):
        expr = (f'let b = ({FLAKE}).lib.rootlessBootstrap {{ pkgs = {STUB}; }}; in '
                'builtins.any (p: builtins.match ".*nix-namespace-root[.]patch" (toString p) != null) b.patches')
        got = nix("eval", "--raw", expr=f"if {expr} then \"yes\" else \"no\"")
        self.assertEqual((got.returncode, got.stdout), (0, "yes"), got.stderr[-600:])

    def test_a_cold_store_builds_at_most_one_derivation(self):
        got = nix("build", "--dry-run", expr=f"({FLAKE}).lib.rootlessBootstrap {{ pkgs = {STUB}; }}")
        self.assertEqual(got.returncode, 0, got.stderr[-600:])
        built = re.search(r"(?:this derivation|these \d+ derivations) will be built:\n((?:\s+/nix/store/\S+\.drv\n?)+)",
                          got.stderr)
        drvs = re.findall(r"\S+\.drv", built.group(1)) if built else []
        self.assertLessEqual(len(drvs), 1, f"would compile {len(drvs)} derivations: {drvs[:5]}")


if __name__ == "__main__":
    unittest.main()
