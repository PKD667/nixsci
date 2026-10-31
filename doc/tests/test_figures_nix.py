"""Property tests for figures (doc/nix/figures.nix). Seeded random inputs, no hypothesis
(not in pyproject.toml).

nix develop path:/home/pkd/code/nixsci --command python3 -m pytest -q doc/tests/test_figures_nix.py
"""
import json
import os
import random
import re
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEED = 20260101
N = 200
FIGS = str(ROOT / "doc/nix/figures.nix")


def run(*args, cwd=None):
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


def nix_figs(body):
    """Evaluate `body` (a Nix expression with `figs` bound to figures.nix) and return its JSON."""
    expr = ('let lib = (builtins.getFlake "path:%s").inputs.nixpkgs.lib; '
            'figs = import %s { inherit lib; }; in %s' % (ROOT, FIGS, body))
    return json.loads(run("nix", "eval", "--impure", "--json", "--expr", expr))


# Law 1 (scaling): for a random fraction f in (0, 1] and a random length W with unit u in
# {pt, cm, mm}, width W f is W*f to within 1e-4 of u, and keeps u. A fraction outside (0, 1] is an
# evaluation error that names the figure.
def test_scaling_law_and_fraction_range():
    rng = random.Random(SEED)
    cases = []
    for _ in range(N):
        w = f"{rng.uniform(1, 1000):.4f}{rng.choice(['pt', 'cm', 'mm'])}"
        f = rng.uniform(1e-4, 1.0) if rng.random() < 0.9 else 1.0
        cases.append({"w": w, "f": f})
    got = nix_figs("map (c: figs.width \"fx/x\" c.w c.f) (builtins.fromJSON (builtins.readFile \"%s\"))"
                   % write_json(cases))
    for c, g in zip(cases, got):
        unit = c["w"][-2:]
        assert g.endswith(unit), (c, g)
        expected = Decimal(c["w"][:-2]) * Decimal(repr(c["f"]))
        assert abs(Decimal(g[:-2]) - expected) <= Decimal("1e-4"), (c, g)

    bad = [{"w": "10pt", "f": f} for f in [0, -0.5, 1.0001, 1.5, 3, 1e9]]
    ok = nix_figs("map (c: (builtins.tryEval (figs.width \"fx/x\" c.w c.f)).success) "
                  "(builtins.fromJSON (builtins.readFile \"%s\"))" % write_json(bad))
    assert ok == [False] * len(bad), (bad, ok)
    p = subprocess.run(["nix", "eval", "--impure", "--raw", "--expr",
                        'let lib = (builtins.getFlake "path:%s").inputs.nixpkgs.lib; '
                        'figs = import %s { inherit lib; }; in figs.width "fx-figs/two" "10pt" 1.5'
                        % (ROOT, FIGS)], text=True, capture_output=True)
    assert p.returncode != 0 and "fx-figs/two" in p.stderr, p.stderr


def write_json(obj):
    fd, name = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(obj, fh)
    return name


# Law 2 (figuresIn): for a random set of file names in a directory, the keys of figuresIn are
# exactly the stems of the *.tex regular files; other files and directories named *.tex are ignored.
def test_figuresin_keys_are_tex_stems(tmp_path):
    rng = random.Random(SEED + 1)
    alphabet = "abcXYZ019-_"
    stems = {"".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8))) for _ in range(N // 4)}
    others = {"".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8))) + ext
              for ext in [".txt", ".pdf", ".tex~", ""] for _ in range(3)}
    d = tmp_path / "figs"
    d.mkdir()
    for s in stems:
        (d / f"{s}.tex").write_text("x")
    for o in others:
        (d / o).write_text("x")
    (d / "subdir.tex").mkdir()
    expected = sorted(stems)
    got = nix_figs(f'builtins.attrNames (figs.figuresIn (/. + "{d}"))')
    assert got == expected


# Law 3 (params): for random attrsets of strings, ints and bools, \figparam{k} in a built figure
# gives exactly the rendered value (bool -> 1 or 0). A key the document does not set, with no
# default, fails the build with the message.
def test_params_render_and_missing_fails_build(tmp_path):
    rng = random.Random(SEED + 2)
    params = {}
    for i in range(12):
        k = f"k{i}-" + "".join(rng.choice("abcdefgh") for _ in range(3))
        kind = rng.choice(["str", "int", "bool"])
        if kind == "str":
            params[k] = "".join(rng.choice("abcdefghXYZ0123456789") for _ in range(rng.randint(1, 10)))
        elif kind == "int":
            params[k] = rng.randint(0, 10**6)
        else:
            params[k] = rng.random() < 0.5
    rendered = {k: ("1" if v is True else "0" if v is False else str(v)) for k, v in params.items()}
    listing = "".join(f"\\texttt{{{k}=\\figparam{{{k}}}}}\\par\n" for k in params)
    fx = make_fixture(tmp_path, listing, params)
    out = Path(run("nix", "build", "--no-link", "--print-out-paths", f"{fx}#default"))
    text = re.sub(r"\s+", " ", run("nix", "shell", "--inputs-from", str(ROOT), "nixpkgs#poppler-utils",
                                   "-c", "pdftotext", str(out / "nixsci/fx-figs/one.pdf"), "-"))
    for k, v in rendered.items():
        assert f"{k}={v}" in text, (k, v, text)

    missing = make_fixture(tmp_path / "m", r"\figparam{absent-key}", params)
    p = subprocess.run(["nix", "build", "-L", "--no-link", f"{missing}#default"], text=True, capture_output=True)
    assert p.returncode != 0 and "no figure parameter `absent-key'" in p.stdout + p.stderr


def tex_value(v):
    return "true" if v is True else "false" if v is False else json.dumps(v)


def make_fixture(where, listing, params):
    where = Path(where)
    (where / "figs").mkdir(parents=True)
    (where / "tex").mkdir()
    (where / "figs/one.tex").write_text(listing)
    (where / "tex/main.tex").write_text(r"\documentclass{article}\begin{document}x\end{document}" + "\n")
    system = run("nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem")
    nix_params = "{ " + " ".join(f"{json.dumps(k)} = {tex_value(v)};" for k, v in params.items()) + " }"
    (where / "flake.nix").write_text("""{
  inputs.nixsci.url = "path:%s";
  inputs.nixpkgs.follows = "nixsci/nixpkgs";
  outputs = { self, nixsci, nixpkgs }: let
    pkgs = nixpkgs.legacyPackages.%s;
    doc = nixsci.lib.doc { inherit pkgs; };
    figs = doc.project { name = "fx-figs"; figures = doc.figuresIn ./figs; };
    document = doc.document {
      name = "fx"; src = ./tex; projects = [ figs ];
      template = doc.template { name = "fx"; figure = { width = "398.3386pt"; }; };
      figures."fx-figs".one = { params = %s; };
    };
  in { packages.%s.default = document.source; };
}
""" % (ROOT, system, nix_params, system))
    run("nix", "flake", "lock", str(where))
    return where
