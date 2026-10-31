"""Opt-in XeLaTeX regression test for a project font in the dev loop.

The font is a project file, so it is at nixsci/fixture-fonts/ in the linked tree. The document names
it by \nixsciroot, which the dev shell's latexmk wrapper sets (the shell exports no TTFONTS or
NIXSCI_TREE any more; doc/nix/doc.nix, env). A build that finds the font has used that path.

pytest -q doc/tests/test_font_dev_nix.py
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_dev_shell_finds_project_font_through_nixsciroot(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "tex").mkdir(parents=True)
    (fixture / "tex/main.tex").write_text(r"""\documentclass{article}
\usepackage{fontspec}
\newfontfamily\fixturefont[Path=\nixsciroot nixsci/fixture-fonts/]{DejaVuSans.ttf}
\begin{document}{\fixturefont Font lookup works.}\end{document}
""")
    (fixture / "flake.nix").write_text("""{
  inputs.nixsci.url = """ + json.dumps("path:" + str(ROOT)) + """;
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
    # The shell starts in a subdirectory of the flake: the root is the flake's, not this one.
    outdir = fixture / "sub"
    outdir.mkdir()
    builddir = tmp_path / "output"
    builddir.mkdir()
    # latexmk here is the shell's wrapper, which passes -usepretex; the build runs outside the tree.
    command = (
        'cd ' + json.dumps(str(builddir)) + '; '
        'latexmk -g -xelatex -interaction=nonstopmode -halt-on-error '
        '"' + str(fixture / "tex/main.tex") + '"'
    )
    run("nix", "develop", str(fixture) + "#default", "--command", "bash", "-c", command, cwd=outdir)
    assert (builddir / "main.pdf").is_file()
    assert not (outdir / ".nixsci").exists()
    assert (fixture / ".nixsci" / "doc" / "font-fixture" / "nixsci" / "fixture-fonts" / "DejaVuSans.ttf").is_file()
