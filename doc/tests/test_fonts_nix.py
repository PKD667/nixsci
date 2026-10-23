"""Property tests for doc/nix/fonts.nix. Run: NIXSCI_INTEGRATION=1 pytest -q doc/tests/test_fonts_nix.py
Each law runs on 40 seeded inputs (random.Random(seed); hypothesis is not in pyproject.toml). Cases
are JSON read by Nix: one `nix eval --json` per law, plus one per rejected case for its message."""
import json, os, random, re, subprocess, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("NIXSCI_INTEGRATION") != "1", reason="opt-in Nix integration")
ROOT = Path(__file__).resolve().parents[2]
SEEDS, FACES = range(40), ["regular", "bold", "italic", "bolditalic"]
FONT_KEYS, BAD = {"family", "dir", "tex", "ext", "faces", "fake", "options"}, set("{}\\%#/\n")
FAMILIES = ["Berkeley", "Lib Erty", "Ok2", "Berkeley", "Other", "Ok2", "a{b", "c/d", "e\\f", "g%h", "h#i"]


def ev(tmp, data, body):
    path = tmp / f"cases-{uuid.uuid4().hex}.json"  # unique: parallel evals must not share a file
    path.write_text(json.dumps(data))
    expr = (f'let lib = (builtins.getFlake "path:{ROOT}").inputs.nixpkgs.lib;\n'
            f'  fonts = import {ROOT}/doc/nix/fonts.nix {{ inherit lib; }};\n'
            f'  cases = builtins.fromJSON (builtins.readFile {path});\nin {body}')
    return subprocess.run(["nix", "eval", "--impure", "--json", "--expr", expr], cwd=tmp, text=True, capture_output=True)


@pytest.fixture
def dirs(tmp_path):
    b, o = tmp_path / "berkeley", tmp_path / "other"
    b.mkdir(), o.mkdir(), (b / "sub").mkdir()
    for n in ["Berkeley.ttf", "Berkeley-Bold.ttf", "README.txt"]:
        (b / n).write_text(n)
    (b / "sub" / "Inner.ttf").write_text("not top level")
    (b / "link.ttf").symlink_to(b / "Berkeley.ttf")
    for n in ["Other.otf", "Other-Italic.otf"]:
        (o / n).write_text(n)
    return {"berkeley": str(b), "other": str(o)}


def gen_mk(rng, d):
    """A random attrset: a valid font about half the time, then with a third chance one wrong value."""
    if rng.random() < 0.5:
        fam, dir_, ext = rng.choice([("Berkeley", d["berkeley"], ".ttf"), ("Other", d["other"], ".otf")])
        c = {"family": fam, "dir": dir_, "ext": ext}
        if rng.random() < 0.5:
            names = ["Berkeley", "Berkeley-Bold"] if fam == "Berkeley" else ["Other", "Other-Italic"]
            c["faces"] = {k: rng.choice(names) for k in rng.sample(FACES, rng.randint(0, 4))}
    else:
        c = {"family": rng.choice(["Lib Erty", "Ok2"]), "tex": rng.choice(["libertine", "inconsolata"])}
    if rng.random() < 0.5:
        c["options"] = rng.choice(["Scale=0.9", "LetterSpace=12, Scale=0.98"])
    if rng.random() < 0.6:
        c["fake"] = {k: rng.choice([1.4, 0, 2.5, "x", True, None]) for k in rng.sample(["bold", "slant"], rng.randint(1, 2))}
    if rng.random() < 0.33:
        rng.choice([
            lambda: c.update(family=rng.choice(FAMILIES)),
            lambda: c.update(ext=3),
            lambda: c.update(faces={"italik": "Berkeley"}),
            lambda: c.update(faces={"italic": "Missing"}),
            lambda: c.update(slant=1),
            lambda: c.update(dir="/nonexistent-nixsci" if "dir" in c else "x"),
            lambda: c.update(dir=3),
            lambda: c.update(fake={"bold": rng.choice(["x", True])}),
            lambda: c.update(options=3),
        ])()
    return c


def is_number(v):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def accepts(c):
    """The spec of mk, restated. A dir font's regular file is <family><ext> unless faces names it."""
    dir, tex, ext, faces = c.get("dir"), c.get("tex"), c.get("ext", ".ttf"), c.get("faces", {})
    fake = c.get("fake", {})
    if not c["family"] or BAD & set(c["family"]) or set(c) - FONT_KEYS or (dir is None) == (tex is None):
        return False
    if dir is not None and (not isinstance(dir, str) or not os.path.isdir(dir)):
        return False
    if tex is not None and not isinstance(tex, str):
        return False
    if not isinstance(ext, str):
        return False
    if not isinstance(c.get("options", ""), str):
        return False
    if not isinstance(faces, dict) or set(faces) - set(FACES) or any(not isinstance(v, str) or BAD & set(v) for v in faces.values()):
        return False
    if dir is not None and any(v and not os.path.isfile(os.path.join(dir, v + ext)) for v in faces.values()):
        return False
    if dir is not None and not faces.get("regular") and not os.path.isfile(os.path.join(dir, c["family"] + ext)):
        return False
    return isinstance(fake, dict) and set(fake) <= {"bold", "slant"} and all(is_number(v) for v in fake.values())


def test_a_mk_accepts_exactly_the_valid_attrsets(dirs, tmp_path):
    """Law (a): mk accepts c iff accepts(c); every rejection message names the family.
    fake.bold and fake.slant are null or numbers; a string there is rejected by mk, not by fontsTex."""
    cases = [gen_mk(random.Random(s), dirs) for s in SEEDS]
    p = ev(tmp_path, cases, "map (c: (builtins.tryEval (builtins.deepSeq (fonts.mk c) true)).success) cases")
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert [accepts(c) for c in cases] == got
    rejects = [c for c, ok in zip(cases, got) if not ok]
    assert 5 <= len(rejects) <= len(cases) - 5, "generator does not exercise both outcomes"
    with ThreadPoolExecutor(8) as pool:
        errs = list(pool.map(lambda c: ev(tmp_path, [c], "builtins.deepSeq (fonts.mk (builtins.head cases)) true"), rejects))
    for c, e in zip(rejects, errs):
        assert e.returncode != 0 and f"doc.font {c['family']}" in e.stderr, (c, e.stderr)


def gen_roles(rng, d):
    roles = {}
    for name in rng.sample(["main", "mono", "serif", "sans", "math"], rng.randint(1, 4)):
        if rng.random() < 0.5:
            fam = rng.choice(["Berkeley", "Other"])
            names, ext = (["Berkeley", "Berkeley-Bold"], ".ttf") if fam == "Berkeley" else (["Other", "Other-Italic"], ".otf")
            roles[name] = {"family": fam, "dir": d[fam.lower()], "ext": ext,
                           "faces": {k: rng.choice(names) for k in rng.sample(FACES, rng.randint(0, 4))},
                           "fake": {k: rng.choice([1.4, 0.15, 2]) for k in rng.sample(["bold", "slant"], rng.randint(0, 2))}}
        else:
            roles[name] = {"family": rng.choice(["Lib", "Mono"]), "tex": rng.choice(["libertine", "inconsolata"]),
                           "ext": rng.choice([".ttf", ".otf"]), "faces": {k: f"X-{k}" for k in rng.sample(FACES, rng.randint(0, 4))}}
    return roles


def parse(line):
    """Fields of one fontsTex line; fails unless it is \\nixsci@font with exactly 11 brace groups."""
    assert re.fullmatch(r"\\nixsci@font(\{[^{}]*\}){11}", line), line
    g = re.findall(r"\{([^{}]*)\}", line)
    return {"role": g[0], "family": g[1], "path": g[2], "ext": g[3], "faces": g[4:8],
            "bold": float(g[8]) if g[8] else None, "slant": float(g[9]) if g[9] else None,
            "options": g[10]}


def expected(role, f):
    fake = f.get("fake", {})
    return {"role": role, "family": f["family"], "path": f"nixsci/fonts/{f['family']}/" if "dir" in f else "",
            "ext": f.get("ext", ".ttf"), "faces": [f.get("faces", {}).get(k, "") for k in FACES],
            "bold": fake.get("bold"), "slant": fake.get("slant"), "options": f.get("options", "")}


def shipped(f):
    """Law (c)'s spec, restated: a named face's file, or for an unnamed face fontspec's default file
    when the directory holds it. The regular face is always there."""
    fam, ext, faces, d = f["family"], f.get("ext", ".ttf"), f.get("faces", {}), Path(f["dir"])
    default = {"regular": fam, "bold": fam + "-Bold", "italic": fam + "-Italic", "bolditalic": fam + "-BoldItalic"}
    out = set()
    for k in FACES:
        if k in faces:
            out.add(faces[k] + ext)
        elif (d / (default[k] + ext)).is_file():
            out.add(default[k] + ext)
    return out


def test_bc_fontsTex_round_trips_and_files_packages_are_exact(dirs, tmp_path):
    """Law (b): each fontsTex line has 11 brace groups, and parsing it returns the input's fields.
    Law (c): files = exactly the shipped() files of each dir font, each once, nothing else;
    texPackages = sorted set of tex names; mk (mk f) == mk f for every font."""
    roles = [gen_roles(random.Random(s), dirs) for s in SEEDS]
    body = ("map (r: { tex = fonts.fontsTex r; packages = fonts.texPackages r; files = fonts.files r;"
            " idem = map (f: fonts.mk (fonts.mk f) == fonts.mk f) (builtins.attrValues r); }) cases")
    p = ev(tmp_path, roles, body)
    assert p.returncode == 0, p.stderr
    for r, got in zip(roles, json.loads(p.stdout)):
        assert [parse(x) for x in got["tex"].splitlines()] == [expected(n, r[n]) for n in sorted(r)]
        want = {(f"nixsci/fonts/{f['family']}/{n}", f"{f['dir']}/{n}") for f in r.values() if "dir" in f
                for n in shipped(f)}
        assert len(got["files"]) == len({e["to"] for e in got["files"]})
        assert {(e["to"], e["from"]) for e in got["files"]} == want
        assert got["packages"] == sorted({f["tex"] for f in r.values() if "tex" in f})
        assert all(got["idem"])
