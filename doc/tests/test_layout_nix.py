"""Law 4 of the layout: the dev loop writes .nixsci at the flake root and nowhere else.

Laws 1-3 (closure, layout, hygiene of the zip) are flake checks over every example document:
`nix build .#checks.<system>.layout-laws`, defined in doc/nix/laws.nix. Only the dev-loop law is
driven from here, because it needs an interactive-style shell in a checkout.

Law 4: starting the dev shell from any subdirectory of a checkout creates .nixsci only at the flake
root, never in the directory the command was started in.

NIXSCI_INTEGRATION=1 pytest -q doc/tests/test_layout_nix.py
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("NIXSCI_INTEGRATION") != "1", reason="opt-in Nix integration")
ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def fixture_flake(fixture: Path):
    system = run("nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem")
    (fixture / "tex").mkdir(parents=True)
    (fixture / "tex/main.tex").write_text(r"""\documentclass{article}
\begin{document}Layout law four.\end{document}
""")
    (fixture / "flake.nix").write_text("""{
  inputs.nixsci.url = """ + json.dumps("path:" + str(ROOT)) + """;
  inputs.nixpkgs.follows = "nixsci/nixpkgs";
  outputs = { self, nixsci, nixpkgs }: let
    pkgs = nixpkgs.legacyPackages.""" + system + """;
    doc = nixsci.lib.doc { inherit pkgs; };
    document = doc.document { name = "layout-law4"; src = ./tex; };
  in { devShells.""" + system + """.default = document.shell; };
}
""")


@pytest.mark.parametrize("depth", [("sub",), ("sub", "deeper", "still")])
def test_dev_shell_in_a_subdirectory_writes_state_only_at_the_root(tmp_path, depth):
    # A git checkout, so that the root is the git toplevel (the same rule as outside git).
    fixture = tmp_path / "checkout"
    fixture.mkdir()
    run("git", "init", "-q", str(fixture))
    fixture_flake(fixture)
    cwd = fixture.joinpath(*depth)
    cwd.mkdir(parents=True)

    run("nix", "develop", "path:" + str(fixture) + "#default", "--command", "true", cwd=cwd)

    assert (fixture / ".nixsci").is_dir(), "the flake root has no .nixsci"
    for d in [cwd] + [cwd.parents[i] for i in range(len(depth))]:
        if d != fixture:
            assert not (d / ".nixsci").exists(), f"{d} has a .nixsci directory"
