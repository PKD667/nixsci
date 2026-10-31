"""Local whole-pipeline test. Temporary fixture repo, never a development worktree.

nix develop --command pytest -q doc/tests/test_remeasure_nix.py
"""
import json
import hashlib
import zipfile
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(*args, cwd=None):
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


@pytest.mark.parametrize("mode", ["contradicted", "unjudged", "failure"])
def test_fresh_runs_feed_new_pdf_and_claim_warnings(tmp_path, monkeypatch, mode):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    observation = tmp_path / "observation"
    observation.write_text("1")
    # This file is the fixture's external apparatus, deliberately changed between measurements.
    (fixture / "measure.py").write_text('''import os, time
from pathlib import Path
from nixsci import lab
observation = Path(os.environ["OBSERVATION_FILE"]).read_text().strip()
if observation == "crash":
    raise RuntimeError("fixture apparatus failed")
lab.record("sample", {"ok": observation == "1", "nonce": str(time.time_ns())})
''')
    (fixture / "analysis.py").write_text('''from nixsci.lab.analysis import use, values
rows = use("probe").table("sample").to_table().to_pylist()
assert len(rows) == 4
values({"nonce": rows[-1]["nonce"]})
''')
    (fixture / "tex").mkdir()
    (fixture / "tex/main.tex").write_text(r'''\documentclass{article}
\usepackage{nixsci}
\begin{document}Nonce: \val{probe/summary.nonce}\end{document}
''')
    repo = "git+file://" + str(ROOT) + "?ref=main"
    (fixture / "flake.nix").write_text('''{
  inputs.nixsci.url = ''' + json.dumps(repo) + ''';
  inputs.nixpkgs.follows = "nixsci/nixpkgs";
  outputs = { self, nixsci, nixpkgs }: let
    system = builtins.currentSystem;
    pkgs = nixpkgs.legacyPackages.${system};
    lab = nixsci.lib.lab { inherit pkgs; project = self; };
    doc = nixsci.lib.doc { inherit pkgs; };
    closure = nixsci.lib.mkExperiment pkgs {
      name = "remeasure-fixture";
      program = "${pkgs.python3}/bin/python";
      args = [ ./measure.py ];
      env.PYTHONPATH = "${nixsci.packages.${system}.lab-py}/${pkgs.python3.sitePackages}";
      env.OBSERVATION_FILE = ''' + json.dumps(str(observation)) + ''';
    };
    exp = lab.experiment {
      name = "remeasure-fixture"; closures.${system} = closure;
      seeds = [ 0 1 ]; replicates = 2;
      resources = { provider = "local"; hosts = 1; };
      data.sample.columns = { ok = "bool"; nonce = "str"; };
    };
    analysis = lab.analysis {
      name = "remeasure-fixture-analysis"; use.probe = exp;
      pipelines.py.script = ./analysis.py;
      claims.ok = { kind = "exact"; value = true;
        of = { use = "probe"; dataset = "sample"; column = "ok"; }; };
    };
    project = doc.project {
      name = "probe";
      data.shared = doc.output analysis "values.json";
      values.summary = doc.output analysis "values.json";
    };
    paper = doc.document { name = "probe-doc"; src = ./tex;
      projects = [ project ]; source = self; };
  in {
    apps.${system} = { measure = exp.run; probe-doc = paper.apps.default; };
    packages.${system} = { probe-doc = paper.pdf; probe-analysis = analysis; probe-publish = paper.publish; };
  };
}
''')
    # Concrete system keeps the fixture flake pure; builtins.currentSystem is not pure-eval safe.
    system = run("nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem")
    p = fixture / "flake.nix"
    p.write_text(p.read_text().replace("system = builtins.currentSystem;", f"system = {json.dumps(system)};"))
    if mode == "unjudged":
        declaration = """      claims.ok = { kind = "exact"; value = true;
        of = { use = "probe"; dataset = "sample"; column = "ok"; }; };
"""
        assert declaration in p.read_text()
        p.write_text(p.read_text().replace(declaration, ""))
    run("git", "init", "-q", str(fixture))
    run("git", "-C", str(fixture), "config", "user.name", "nixsci test")
    run("git", "-C", str(fixture), "config", "user.email", "test@invalid")
    run("git", "-C", str(fixture), "add", ".")
    run("git", "-C", str(fixture), "commit", "-qm", "fixture")
    run("nix", "flake", "lock", str(fixture))
    run("git", "-C", str(fixture), "add", "flake.lock")
    run("git", "-C", str(fixture), "commit", "-qm", "pin inputs")
    run("nix", "run", str(fixture) + "#measure", cwd=fixture)
    baseline = (fixture / "lab.lock").read_bytes()
    assert len(json.loads(baseline)["runs"]["remeasure-fixture"]) == 4
    run("git", "-C", str(fixture), "add", "lab.lock")
    run("git", "-C", str(fixture), "commit", "-qm", "baseline measurements")
    old_analysis = Path(run("nix", "build", "--no-link", "--print-out-paths", str(fixture) + "#probe-analysis"))
    old_nonce = json.loads((old_analysis / "values.json").read_text())["nonce"]
    published = Path(run("nix", "build", "--no-link", "--print-out-paths", str(fixture) + "#probe-publish"))
    for line in (published / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((published / name).read_bytes()).hexdigest() == digest
    with zipfile.ZipFile(published / "probe-doc-artifact.zip") as archive:
        assert any(n.startswith("data/probe/summary/view/probe/sample/") and n.endswith(".parquet") for n in archive.namelist())
        assert "data/probe/summary/view/probe/runs.json" in archive.namelist()
        assert "data/probe/shared/view/probe/runs.json" in archive.namelist()
    observation.write_text("crash" if mode == "failure" else "0")
    output = tmp_path / "fresh"
    command = ["nix", "run", str(fixture) + "#probe-doc", "--", "--remeasure", "--level", "0.99",
               "--output", str(output)]
    if mode == "failure":
        result = subprocess.run(command, cwd=fixture, text=True, capture_output=True)
        assert result.returncode != 0
        assert "diagnostics retained" in result.stderr
        assert not list(output.glob("*.pdf"))
        assert list((output / "diagnostics").rglob("manifest.json"))
        assert (fixture / "lab.lock").read_bytes() == baseline
        assert run("git", "-C", str(fixture), "status", "--porcelain") == ""
        return
    run(*command, cwd=fixture)
    assert (fixture / "lab.lock").read_bytes() == baseline
    assert run("git", "-C", str(fixture), "status", "--porcelain") == ""
    locks = json.loads((output / "locks.json").read_text())
    fresh = next(iter(locks.values()))["remeasure-fixture"]
    assert len(fresh) == 4
    assert {r["sha256"] for r in fresh}.isdisjoint({r["sha256"] for r in json.loads(baseline)["runs"]["remeasure-fixture"]})
    report = json.loads((output / "reports/summary.json").read_text())
    assert report["contradicted"] == (1 if mode == "contradicted" else 0)
    assert report["unjudged"] == (1 if mode == "unjudged" else 0)
    pdf = output / "probe-doc.pdf"
    assert pdf.is_file()
    # The footer and nonce must come from fresh measurements, not a recycled baseline PDF.
    pkgs = 'let f=builtins.getFlake ' + json.dumps(str(ROOT)) + ';in f.inputs.nixpkgs.legacyPackages.' + system + '.poppler-utils'
    text = run("nix", "shell", "--impure", "--expr", pkgs, "-c", "pdftotext", str(pdf), "-")
    assert "REMEASURED" in text
    assert ("1 contradicted" if mode == "contradicted" else "1 unjudged") in text
    assert old_nonce not in text
