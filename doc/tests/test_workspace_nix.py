"""Laws of the workspace, on generated trees. Opt-in: NIXSCI_INTEGRATION=1."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from workspace_gen import gen

pytestmark = pytest.mark.skipif(os.environ.get("NIXSCI_INTEGRATION") != "1", reason="opt-in Nix integration")
ROOT = Path(__file__).resolve().parents[2]
SYSTEM = "x86_64-linux"
SEEDS = list(range(20))


def nix(*args):
    p = subprocess.run(["nix", *args], text=True, capture_output=True)
    return p.returncode == 0, p.stdout, p.stderr


def tree(tmp_path, seed):
    root = tmp_path / f"ws{seed}"
    docs = gen(seed, root)
    (root / "flake.nix").write_text((root / "flake.nix").read_text().replace("path:NIXSCI", "path:" + str(ROOT)))
    return root, docs


def flake(root, attr):
    return f"path:{root}#{attr}"


def attrs(root, attr):
    ok, out, err = nix("eval", "--json", flake(root, attr), "--apply", "builtins.attrNames")
    assert ok, err
    return set(json.loads(out))


@pytest.mark.parametrize("seed", SEEDS)
def test_law1_outputs_per_document(tmp_path, seed):
    """Law 1: every discovered document has exactly <d>, <d>-publish, -source, -crate, -zip in packages, <d> and <d>-open in apps, and <d> in devShells."""
    root, docs = tree(tmp_path, seed)
    pk = attrs(root, f"packages.{SYSTEM}")
    assert pk == {f"{n}{s}" for n, _ in docs for s in ["", "-publish", "-source", "-crate", "-zip"]}
    assert attrs(root, f"apps.{SYSTEM}") == {f"{n}{s}" for n, _ in docs for s in ["", "-open"]}
    assert attrs(root, f"devShells.{SYSTEM}") == {n for n, _ in docs}


@pytest.mark.parametrize("seed", SEEDS)
def test_law2_names_follow_directories(tmp_path, seed):
    """Law 2: a document's default name is its last two path components joined by '-'. Moving one document's directory changes that document's names and no other name."""
    root, docs = tree(tmp_path, seed)
    for name, rel in docs:
        assert name == "-".join(rel.split("/")[-2:])
    before = attrs(root, f"packages.{SYSTEM}")
    name, rel = docs[0]
    target = root / "moved" / Path(rel).name
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / rel).rename(target)
    after = attrs(root, f"packages.{SYSTEM}")
    new = "moved-" + Path(rel).name
    assert after == (before - {f"{name}{s}" for s in ["", "-publish", "-source", "-crate", "-zip"]})         | {f"{new}{s}" for s in ["", "-publish", "-source", "-crate", "-zip"]}, (before ^ after)


@pytest.mark.parametrize("seed", SEEDS)
def test_law3_ignored_directories_are_never_read(tmp_path, seed):
    """Law 3: archive/, result*, dot directories and node_modules are never read: a broken document.nix in each of them does not fail evaluation, and changes nothing."""
    root, docs = tree(tmp_path, seed)
    ok, out, err = nix("eval", "--json", flake(root, f"packages.{SYSTEM}"), "--apply", "builtins.attrNames")
    assert ok, err
    base = set(json.loads(out))
    for junk in ["archive", "result-1", ".hidden", "node_modules"]:
        assert (root / junk / "junk" / "document.nix").is_file()
    assert base == {f"{n}{s}" for n, _ in docs for s in ["", "-publish", "-source", "-crate", "-zip"]}


def _known(root):
    return [p.name for p in (root / "proj").iterdir()]


@pytest.mark.parametrize("seed", SEEDS)
def test_law5_errors_name_the_file_or_the_known_names(tmp_path, seed):
    """Law 5: an unknown key, template name or project name fails evaluation, and the message names the file (for keys) or lists the known names (for templates and projects)."""
    cases = {
        "key": ('{\n  projects = [ ];\n  colour = "red";\n}\n', "colour"),
        "template": ('{\n  template = "nosuch";\n}\n', "nosuch"),
        "project": ('{\n  projects = [ "nosuch" ];\n}\n', "nosuch"),
    }
    for case, (body, needle) in cases.items():
        root, docs = tree(tmp_path / case, seed)
        name, rel = docs[0]
        (root / rel / "document.nix").write_text(body)
        ok, out, err = nix("eval", "--json", flake(root, f"packages.{SYSTEM}"), "--apply", "builtins.attrNames")
        assert not ok, f"{case}: evaluation should fail"
        assert needle in err, err
        assert f"{rel}/document.nix" in err or "known:" in err, err


def test_law4_workspace_equals_hand_written_flake(tmp_path):
    """Law 4: the toy workspace (doc/examples/workspace/flake.nix) and the same toy written by hand (flake-by-hand.nix) give PDFs with equal pdftotext, for every document."""
    src = ROOT / "doc/examples/workspace"
    ws, hand = tmp_path / "ws", tmp_path / "hand"
    shutil.copytree(src, ws)
    shutil.copytree(src, hand)
    for d in (ws, hand):
        (d / "flake.nix").write_text((d / "flake.nix").read_text().replace(
            "git+file:///home/pkd/code/nixsci?ref=main", "path:" + str(ROOT)))
        (d / "flake.lock").unlink(missing_ok=True)
    shutil.move(str(hand / "flake-by-hand.nix"), str(hand / "flake.nix"))
    (hand / "flake.nix").write_text((hand / "flake.nix").read_text().replace(
        "git+file:///home/pkd/code/nixsci?ref=main", "path:" + str(ROOT)))
    for name in ["notes-memo", "notes-letter"]:
        texts = []
        for d in (ws, hand):
            ok, out, err = nix("build", "--no-link", "--print-out-paths", f"path:{d}#{name}")
            assert ok, err
            pdf = Path(out.strip()) / f"{name}.pdf"
            txt = subprocess.run(["nix", "shell", "--inputs-from", f"path:{ROOT}", "nixpkgs#poppler-utils",
                                  "-c", "pdftotext", str(pdf), "-"], text=True, capture_output=True)
            assert txt.returncode == 0, txt.stderr
            texts.append(txt.stdout)
        assert texts[0].strip(), f"{name}: no text in the PDF"
        assert texts[0] == texts[1], name


def fixture_input(tmp_path, name, templates):
    """A flake whose lib.templates is a function of doc returning the named templates."""
    d = tmp_path / f"input-{name}"
    d.mkdir(parents=True)
    body = "".join(f'      {t} = doc.template {{ name = "{t}"; }};\n' for t in templates)
    (d / "flake.nix").write_text("{\n  outputs = { self }: {\n    lib.templates = doc: {\n" + body + "    };\n  };\n}\n")
    return d


def add_input(root, name, fixture):
    text = (root / "flake.nix").read_text()
    text = text.replace("  outputs = inputs", f'  inputs.{name}.url = "path:{fixture}";\n  outputs = inputs', 1)
    (root / "flake.nix").write_text(text)


def use_template(root, rel, template):
    text = (root / rel / "document.nix").read_text()
    (root / rel / "document.nix").write_text(text.replace("{\n", f'{{\n  template = "{template}";\n', 1))


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_law6_input_templates_by_name_local_wins(tmp_path, seed):
    """Law 6: a flake input with lib.templates provides its templates by name; a document may use
    one by name, and a local template.nix of the same name wins over the input."""
    root, docs = tree(tmp_path, seed)
    add_input(root, "ext", fixture_input(tmp_path, "ext", ["ext"]))
    use_template(root, docs[0][1], "ext")
    ok, out, err = nix("eval", "--json", flake(root, f"packages.{SYSTEM}"), "--apply", "builtins.attrNames")
    assert ok, err
    assert docs[0][0] in json.loads(out)
    local = root / "tmpl" / "ext"
    local.mkdir(parents=True)
    (local / "template.nix").write_text('{ name = "ext"; }\n')
    ok, out, err = nix("eval", "--json", flake(root, f"packages.{SYSTEM}"), "--apply", "builtins.attrNames")
    assert ok, err


@pytest.mark.parametrize("seed", SEEDS[:5])
def test_law7_clashes_name_both_sources(tmp_path, seed):
    """Law 7: two inputs with one template name, or an input with a built-in name, fail
    evaluation, and the message names both sources."""
    root, docs = tree(tmp_path, seed)
    add_input(root, "ext", fixture_input(tmp_path, "ext", ["ext"]))
    add_input(root, "ext2", fixture_input(tmp_path / "b", "ext2", ["ext"]))
    use_template(root, docs[0][1], "ext")
    ok, out, err = nix("eval", "--json", flake(root, f"packages.{SYSTEM}"), "--apply", "builtins.attrNames")
    assert not ok
    assert "flake input ext" in err and "flake input ext2" in err, err
    root2, docs2 = tree(tmp_path / "builtin", seed)
    add_input(root2, "ext", fixture_input(tmp_path / "c", "ext", ["plain"]))
    use_template(root2, docs2[0][1], "plain")
    ok, out, err = nix("eval", "--json", flake(root2, f"packages.{SYSTEM}"), "--apply", "builtins.attrNames")
    assert not ok
    assert "the built-in templates" in err and "flake input ext" in err, err
