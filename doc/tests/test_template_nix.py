"""Property tests for doc/nix/template.nix, and one build check on the examples.

Opt-in (needs Nix and the pinned nixpkgs; no hypothesis in pyproject, so the cases are drawn from
seeded `random.Random(seed)`):

    NIXSCI_INTEGRATION=1 nix develop --command pytest -q doc/tests/test_template_nix.py

Each test states its law in its docstring and checks it over N_CASES seeded inputs. Nix is run once
per test where the law can be read from a batch; where the law is about an error message, once per
case, because a batch cannot return messages.
"""
import json
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("NIXSCI_INTEGRATION") != "1", reason="opt-in Nix integration")
ROOT = Path(__file__).resolve().parents[2]
N_CASES = 32
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Fixed expression prelude. `tl` is template.nix as the flake sees it; `doc` is doc.nix.
PRELUDE = f"""
let
  f = builtins.getFlake "path:{ROOT}";
  pkgs = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
  doc = import {ROOT}/doc/nix/doc.nix {{ inherit pkgs; crateTool = null; }};
  tl = import {ROOT}/doc/nix/template.nix {{ inherit pkgs; toolkit = {ROOT}/doc/files; }};
  src = pkgs.writeTextDir "main.tex" "x";
in
"""


def nix_file(body, *, json_out=False, raw=False):
    """Evaluate PRELUDE + body. Returns the CompletedProcess; the caller reads stdout or stderr."""
    with tempfile.NamedTemporaryFile("w", suffix=".nix", delete=False) as fh:
        fh.write(PRELUDE + body)
        path = fh.name
    try:
        args = ["nix", "eval", "--impure", "--extra-experimental-features", "nix-command flakes", "--file", path]
        if json_out:
            args.append("--json")
        if raw:
            args.append("--raw")
        return subprocess.run(args, text=True, capture_output=True)
    finally:
        os.unlink(path)


def nstr(s):
    """A Nix string literal for s (ASCII, no interpolation characters are generated)."""
    assert "$" not in s and '"' not in s and "\\" not in s
    return f'"{s}"'


def nlist(items):
    return "[ " + " ".join(items) + " ]"


# ---------------------------------------------------------------------------------------------
# (1) Round trip. Law: for a random class, use list, style, medium and projects, the lines
# generatedLines emits parse back to exactly the input, and no line is extra or missing.

CLASS_NAMES = ["article", "beamer", "report"]
CLASS_OPTS = ["11pt", "12pt", "aspectratio=169", "draft", "handout"]
USE_NAMES = ["booktabs", "xcolor", "mathtools", "ulem", "siunitx", "enumitem", "cleveref"]
USE_OPTS = ["", "dvipsnames", "normalem", "svgnames,table"]
STYLES = ["deck", "theme", "house"]
MEDIUMS = ["slides", "paper", "poster"]
PROJECT_NAMES = ["p0", "p1", "p2"]

LINE = re.compile(r"^\\nixsci@(\w+)((?:\{[^{}]*\})*)$")


def draw_roundtrip_case(rng):
    cls = None
    if rng.random() < 0.6:
        cls = {"name": rng.choice(CLASS_NAMES), "options": rng.sample(CLASS_OPTS, rng.randint(0, 3))}
    uses = [(rng.choice(USE_NAMES), rng.choice(USE_OPTS)) for _ in range(rng.randint(0, 4))]
    style = rng.choice(STYLES) if rng.random() < 0.5 else None
    medium = rng.choice(MEDIUMS) if rng.random() < 0.5 else None
    projects = []
    for name in rng.sample(PROJECT_NAMES, rng.randint(0, 3)):
        projects.append({"name": name,
                         "style": name if rng.random() < 0.5 else None,
                         "bib": name if rng.random() < 0.5 else None})
    return {"cls": cls, "uses": uses, "style": style, "medium": medium, "projects": projects}


def nix_case(c):
    parts = []
    if c["cls"] is not None:
        opts = ",".join(c["cls"]["options"])
        parts.append(f'class = {{ name = {nstr(c["cls"]["name"])}; options = {nlist(nstr(o) for o in c["cls"]["options"])}; }};')
    if c["medium"] is not None:
        parts.append(f'medium = {nstr(c["medium"])};')
    if c["uses"]:
        use_items = []
        for n, o in c["uses"]:
            # Both spellings: a string when there are no options, { name = options; } otherwise.
            use_items.append(nstr(n) if o == "" else f'{{ {n} = {nstr(o)}; }}')
        parts.append(f'use = {nlist(use_items)};')
    if c["style"] is not None:
        # A string path: generatedLines takes the basename, so no store copy is needed.
        parts.append(f'style = {nstr("/tmp/nixsci-roundtrip/" + c["style"] + ".sty")};')
    projects = [
        f'{{ name = {nstr(p["name"])}; style = {nstr(p["style"]) if p["style"] else "null"}; '
        f'bib = {nstr(p["bib"]) if p["bib"] else "null"}; }}'
        for p in c["projects"]
    ]
    return f'tl.generatedLines {{ template = tl.template {{ name = "t"; {" ".join(parts)} }}; projects = {nlist(projects)}; }}'


def expected_structure(c):
    """What the input says, in the parse's shape."""
    out = {"class": None, "medium": None, "use": [], "style": None, "project": [], "bib": []}
    if c["cls"] is not None:
        out["class"] = (c["cls"]["name"], list(c["cls"]["options"]))
    out["medium"] = c["medium"]
    out["use"] = [(n, o) for n, o in c["uses"]]
    out["style"] = c["style"]
    out["project"] = [p["name"] for p in c["projects"] if p["style"]]
    out["bib"] = [p["name"] for p in c["projects"] if p["bib"]]
    return out


def parse_generated(text):
    out = {"class": None, "medium": None, "use": [], "style": None, "project": [], "bib": []}
    for line in text.splitlines():
        if not line.strip():
            continue
        m = LINE.match(line)
        assert m, f"not a generated macro line: {line!r}"
        macro, rest = m.group(1), re.findall(r"\{([^{}]*)\}", m.group(2))
        if macro == "class":
            assert len(rest) == 2, line
            out["class"] = (rest[0], rest[1].split(",") if rest[1] else [])
        elif macro == "medium":
            out["medium"] = rest[0]
        elif macro == "use":
            out["use"].append((rest[0], rest[1]))
        elif macro == "style":
            out["style"] = rest[0]
        elif macro in ("project", "bib"):
            out[macro].append(rest[0])
        else:
            raise AssertionError(f"unexpected macro {macro}")
    return out


def test_generated_lines_round_trip():
    """Law: parse(generatedLines(input)) == input, for random class/use/style/medium/projects.
    Checked by evaluating generatedLines on N_CASES seeded inputs in one Nix call."""
    rng = random.Random(1)
    cases = [draw_roundtrip_case(rng) for _ in range(N_CASES)]
    body = "[ " + " ".join(f"({nix_case(c)})" for c in cases) + " ]"
    r = nix_file(body, json_out=True)
    assert r.returncode == 0, r.stderr
    texts = json.loads(r.stdout)
    assert len(texts) == N_CASES
    for c, text in zip(cases, texts):
        assert parse_generated(text) == expected_structure(c), (c, text)


# ---------------------------------------------------------------------------------------------
# (2) Packages. Law: a package list of TeX Live names and the equivalent function ps: [ ... ]
# resolve to the same package names. Base names (see template.nix baseNames) are accepted in a
# list and add nothing, so they are left out of the function, which may only name set members.

SET_NAMES = ["booktabs", "xcolor", "pgf", "siunitx", "enumitem", "cleveref", "beamer", "mathtools",
             "ulem", "natbib", "biblatex"]
BASE_NAMES = ["tikz", "graphicx", "color"]


def test_package_list_and_function_give_same_set():
    """Law: pkgNames [names] and pkgNames (ps: [ps.n ...]) select the same TeX Live packages, in the
    same order, where the list may add base names that select nothing."""
    rng = random.Random(2)
    cases = [[rng.choice(SET_NAMES + BASE_NAMES) for _ in range(rng.randint(0, 5))] for _ in range(N_CASES)]
    items = []
    for names in cases:
        set_only = [n for n in names if n in SET_NAMES]
        fn = "(ps: [ " + " ".join(f"ps.{n}" for n in set_only) + " ])"
        items.append(f"{{ list = sel {nlist(nstr(n) for n in names)}; fn = sel {fn}; }}")
    body = ("let sel = x: map (p: (p.pname or (builtins.head p.pkgs).pname)) (tl.pkgNames x pkgs.texlive); "
            "in [ " + " ".join(items) + " ]")
    r = nix_file(body, json_out=True)
    assert r.returncode == 0, r.stderr
    results = json.loads(r.stdout)
    assert len(results) == N_CASES
    for names, res in zip(cases, results):
        set_only = [n for n in names if n in SET_NAMES]
        assert res["fn"] == set_only, (names, res)
        assert res["list"] == set_only, (names, res)


def test_base_names_are_in_texlivesmall():
    """Law: every name in template.nix baseNames is a .sty file texliveSmall ships with an empty
    package list, so accepting it in a list and adding nothing is correct. A derivation runs
    kpsewhich under env -i, as the README's zip command would."""
    names = ["amssymb", "amsthm", "array", "bm", "calc", "color", "dcolumn", "expl3", "fontenc", "graphicx",
             "helvet", "ifpdf", "ifthen", "inputenc", "keyval", "lmodern", "longtable", "multicol", "pifont",
             "rotating", "scrlayer-scrpage", "subcaption", "tabularx", "textcomp", "tikz", "trig", "verbatim",
             "xparse", "xspace"]
    # The list in template.nix must be exactly these names, so a drift fails here.
    listed = nix_file("tl.baseNames", json_out=True)
    assert listed.returncode == 0, listed.stderr
    assert sorted(json.loads(listed.stdout)) == sorted(names)
    expr = ("pkgs.runCommand \"base-names\" { nativeBuildInputs = [ (pkgs.texliveSmall.withPackages (ps: [ ])) ]; } ''\n"
            "  for n in " + " ".join(names) + "; do\n"
            "    env -i PATH=\"$PATH\" TEXMFHOME=/nonexistent kpsewhich $n.sty >/dev/null || { echo \"missing: $n\" >&2; exit 1; }\n"
            "  done\n  touch $out\n''")
    with tempfile.NamedTemporaryFile("w", suffix=".nix", delete=False) as fh:
        fh.write(PRELUDE + expr)
        path = fh.name
    try:
        r = subprocess.run(["nix", "build", "--impure", "--no-link", "--file", path],
                           text=True, capture_output=True)
    finally:
        os.unlink(path)
    assert r.returncode == 0, r.stderr[-2000:]


# ---------------------------------------------------------------------------------------------
# (3) Fonts and engine. Law: a template with a non-empty fonts attrset fails evaluation iff its
# engine is pdflatex, and the message says so. With lualatex or xelatex it never fails.

ENGINES = ["pdflatex", "lualatex", "xelatex"]
ROLES = ["main", "sans", "mono", "display"]


def test_fonts_fail_exactly_under_pdflatex():
    """Law: (template with fonts and engine E) fails iff fonts nonempty and E == pdflatex; the
    failure message contains 'fonts need lualatex or xelatex'. One Nix call per case."""
    rng = random.Random(3)
    for _ in range(N_CASES):
        engine = rng.choice(ENGINES)
        roles = rng.sample(ROLES, rng.randint(0, 3))
        fonts = "{ " + " ".join(
            f'{r} = doc.font {{ family = "Fam{i}"; tex = "libertinus"; }};' for i, r in enumerate(roles)) + " }"
        body = f'(tl.template {{ name = "f"; engine = {nstr(engine)}; fonts = {fonts}; }}).engine'
        r = nix_file(body, raw=True)
        should_fail = bool(roles) and engine == "pdflatex"
        assert (r.returncode != 0) == should_fail, (engine, roles, r.stderr[-400:])
        if should_fail:
            assert "fonts need lualatex or xelatex" in r.stderr, r.stderr[-400:]
        else:
            assert r.stdout.strip() == engine


# ---------------------------------------------------------------------------------------------
# (4) Meta. Law: a document's meta evaluation fails iff a required slot is missing or a given key
# is not a slot; and the message names every such slot.

SLOTS = ["title", "authors", "doi", "venue", "period"]


def test_meta_fails_iff_required_missing_or_unknown_given():
    """Law: evaluation of meta fails iff (a required slot is missing) or (a key given is not a
    slot); the message names each missing required slot and each unknown key. One Nix call per
    case, because the message is what is checked."""
    rng = random.Random(4)
    for _ in range(N_CASES):
        kinds = {s: rng.choice(["required", "optional"]) for s in rng.sample(SLOTS, rng.randint(1, 4))}
        given = [s for s in kinds if rng.random() < 0.6]
        unknown = ["bogus"] if rng.random() < 0.3 else []
        missing = [s for s, k in kinds.items() if k == "required" and s not in given]
        meta_slots = "{ " + " ".join(f"{s} = {nstr(k)};" for s, k in kinds.items()) + " }"
        meta_given = "{ " + " ".join(f'{s} = "v";' for s in given + unknown) + " }"
        body = (
            f'let d = doc.document {{ name = "m"; inherit src; '
            f'template = doc.template {{ name = "m"; meta = {meta_slots}; render = _: ""; }}; '
            f'meta = {meta_given}; }}; in builtins.deepSeq d.meta "ok"'
        )
        r = nix_file(body, raw=True)
        should_fail = bool(missing or unknown)
        assert (r.returncode != 0) == should_fail, (kinds, given, unknown, r.stdout, r.stderr[-400:])
        if should_fail:
            for s in missing:
                assert f"{s} is required" in r.stderr, (s, r.stderr[-400:])
            for s in unknown:
                assert f"{s} is not a slot" in r.stderr, (s, r.stderr[-400:])


# ---------------------------------------------------------------------------------------------
# Build check. Law: the example documents still build, and their pdftotext equals the fixture,
# which was taken from the base commit 875e6e2 (before this change) with the same pdftotext.

@pytest.mark.parametrize("attr,pdf", [("example-paper", "example.pdf"), ("example-draft", "example-draft.pdf")])
def test_example_pdftotext_unchanged(attr, pdf):
    """Law: nix build path:ROOT#<attr> succeeds and its PDF's pdftotext -layout equals the
    fixture from the base commit."""
    r = subprocess.run(["nix", "build", "--no-link", "--print-out-paths", f"path:{ROOT}#{attr}"],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr[-2000:]
    out = r.stdout.strip().splitlines()[-1]
    pop = subprocess.run(["nix", "build", "--impure", "--no-link", "--print-out-paths", "--expr",
                          'let f = builtins.getFlake "path:%s"; in '
                          'f.inputs.nixpkgs.legacyPackages.x86_64-linux.poppler-utils' % ROOT],
                         text=True, capture_output=True)
    assert pop.returncode == 0, pop.stderr
    bin_ = pop.stdout.strip().splitlines()[-1] + "/bin/pdftotext"
    text = subprocess.run([bin_, "-layout", f"{out}/{pdf}", "-"], text=True, capture_output=True)
    assert text.returncode == 0, text.stderr
    expected = (FIXTURES / f"{attr}.pdftotext").read_text()
    assert text.stdout == expected
