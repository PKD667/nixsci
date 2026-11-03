"""What a document's TeX does with what Nix gives it: the media of the built-in templates, the values an
analysis writes, the DRAFT mark, and what `publish` waits for. Each builds real documents, so they need
nix and the pinned nixpkgs, and run in the dev shell:

    nix develop --command pytest -q doc/tests/test_typeset_nix.py
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PRELUDE = f"""
let
  f = builtins.getFlake "path:{ROOT}";
  pkgs = f.inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}};
  doc = f.lib.doc {{ inherit pkgs; }};
  source = text: pkgs.writeTextDir "main.tex" text;
in
"""


def nix(*args, check=True):
    return subprocess.run(["nix", *args], text=True, capture_output=True, check=check)


def build(body):
    """Build the derivation PRELUDE + body evaluates to; the CompletedProcess, whether or not it built."""
    return nix("build", "--impure", "--no-link", "--print-out-paths", "--expr", PRELUDE + body, check=False)


def text_of(pdf):
    out = nix("shell", "--inputs-from", f"path:{ROOT}", "nixpkgs#poppler-utils", "-c", "pdftotext", str(pdf), "-").stdout
    return re.sub(r"\s+", " ", out)


def pdf_in(done, name):
    assert done.returncode == 0, done.stderr[-2000:]
    return Path(done.stdout.strip().splitlines()[-1]) / f"{name}.pdf"


# A document of each built-in template, with the one sentence it must typeset.
DOCUMENTS = {
    "paper": r"\documentclass{article}\usepackage{nixsci}\begin{document}typeset ok\end{document}",
    "slides": r"\documentclass{beamer}\usepackage{nixsci}\begin{document}\begin{frame}typeset ok\end{frame}\end{document}",
    "poster": r"\documentclass[a0paper]{tikzposter}\usepackage{nixsci}\begin{document}\block{Claim}{typeset ok}\end{document}",
}


@pytest.mark.parametrize("template", sorted(DOCUMENTS))
def test_a_document_on_every_builtin_template_can_load_nixsci(template):
    """Law: the toolkit knows the medium each built-in template names (slides used to fail with
    "unknown medium", and paper and poster were never defined)."""
    done = build(f'(doc.document {{ name = "t"; template = doc.templates.{template}; src = source {json.dumps(DOCUMENTS[template])}; }}).pdf')
    assert "typeset ok" in text_of(pdf_in(done, "t"))


def test_values_with_tex_special_characters_print_as_written():
    """Law: a string an analysis writes is text, whatever it contains. A `%` used to comment out the
    rest of the line and the whole document failed to build."""
    values = {"share": "50%", "label": "fast_path", "odd": "a&b #1 $x {w}", "median": 0.30000000000000004}
    tex = r"\documentclass{article}\usepackage{nixsci}\begin{document}[\val{p/v.share}] [\val{p/v.label}] [\val{p/v.odd}] [\val{p/v.median}]\end{document}"
    done = build(f'''let p = doc.project {{ name = "p"; values.v = pkgs.writeText "v.json" (builtins.toJSON (builtins.fromJSON {json.dumps(json.dumps(values))})); }};
      in (doc.document {{ name = "t"; src = source {json.dumps(tex)}; projects = [ p ]; }}).pdf''')
    assert "[50%] [fast_path] [a&b #1 $x {w}] [0.30000000000000004]" in text_of(pdf_in(done, "t")).replace("fast path", "fast_path")


def test_a_value_key_TeX_cannot_name_is_an_error_naming_it():
    done = build('''let p = doc.project { name = "p"; values.v = pkgs.writeText "v.json" ''{"bad%key": 1}''; };
      in (doc.document { name = "t"; src = source "x"; projects = [ p ]; }).generated''')
    assert done.returncode != 0 and "value key p/v.bad%key has a character TeX cannot take in a name" in done.stderr


DRAFT = '''let p = doc.project {{ name = "p"; data.x = doc.static (pkgs.writeText "x.csv" "1"); }};
      in (doc.document {{ name = "t"; template = doc.templates.paper; src = source {tex}; projects = [ p ]; }}).pdf'''


def test_a_draft_whose_tex_never_loads_nixsci_does_not_build_unmarked():
    """Law: unclean inputs make a draft, and a draft says so on every page, or it does not exist."""
    done = build(DRAFT.format(tex=json.dumps(r"\documentclass{article}\begin{document}x\end{document}")))
    assert done.returncode != 0
    assert "does not load nixsci, so this PDF would not show its DRAFT or REMEASURED mark" in done.stderr


def test_a_draft_that_loads_nixsci_carries_the_mark():
    done = build(DRAFT.format(tex=json.dumps(r"\documentclass{article}\usepackage{nixsci}\begin{document}x\end{document}")))
    assert "DRAFT: 1 untraced input(s); not for publication" in text_of(pdf_in(done, "t"))


def test_publish_is_not_built_before_the_zip_has_compiled_alone():
    """Law: what `publish` ships, it has checked: its derivation depends on the zip check."""
    drv = nix("eval", "--impure", "--raw", "--expr", PRELUDE +
              '(doc.document { name = "t"; src = source "x"; source = { rev = "abc"; }; }).publish.drvPath').stdout
    needs = subprocess.run(["nix-store", "--query", "--requisites", drv], text=True, capture_output=True, check=True).stdout
    assert re.search(r"-t-zip-check\.drv$", needs, re.M)
