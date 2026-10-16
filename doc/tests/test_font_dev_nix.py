"""Opt-in XeLaTeX regression test for fonts in the assembled document tree.

NIXSCI_INTEGRATION=1 pytest -q doc/tests/test_font_dev_nix.py
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


def test_dev_shell_finds_project_font_by_filename(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "tex").mkdir(parents=True)
    (fixture / "tex/main.tex").write_text(r"""\documentclass{article}
\usepackage{fontspec}
\newfontfamily\fixturefont{DejaVuSans.ttf}
\begin{document}{\fixturefont Font lookup works.}\end{document}
""")
    (fixture / "flake.nix").write_text("""{
  inputs.nixsci.url = """ + json.dumps("git+file://" + str(ROOT) + "?ref=main") + """;
  inputs.nixpkgs.follows = "nixsci/nixpkgs";
  outputs = { self, nixsci, nixpkgs }: let
    system = """ + json.dumps(run("nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem")) + """;
    pkgs = nixpkgs.legacyPackages.${system};
    doc = nixsci.lib.doc { inherit pkgs; };
    fonts = doc.project {
      name = "fixture-fonts";
      files.DejaVuSans = "${pkgs.dejavu_fonts}/share/fonts/truetype/DejaVuSans.ttf";
    };
    document = doc.document { name = "font-fixture"; src = ./tex; projects = [ fonts ];
      template = doc.template { name = "font-fixture"; engine = "xelatex"; };
    };
  in { devShells.${system}.default = document.shell; };
}
""")
    run("nix", "flake", "lock", str(fixture))
    outdir = tmp_path / "shell-cwd"
    outdir.mkdir()
    builddir = tmp_path / "output"
    builddir.mkdir()
    command = (
        'cd ' + json.dumps(str(builddir)) + '; '
        'echo "kpsewhich: $(kpsewhich --format=\'truetype fonts\' DejaVuSans.ttf)"; '
        'test -n "$(kpsewhich --format=\'truetype fonts\' DejaVuSans.ttf)"; '
        'latexmk -g -xelatex -interaction=nonstopmode -halt-on-error '
        '"' + str(fixture / "tex/main.tex") + '"'
    )
    output = run("nix", "develop", str(fixture) + "#default", "--command", "bash", "-c", command,
                 cwd=outdir)
    assert "DejaVuSans.ttf" in output
    assert (builddir / "main.pdf").is_file()
