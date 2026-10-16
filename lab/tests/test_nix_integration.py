"""Local Nix boundary checks; opt in with NIXSCI_INTEGRATION=1. No SSH.

NIXSCI_INTEGRATION=1 nix develop --command pytest -q lab/tests/test_nix_integration.py
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from nixsci import lab
from nixsci.lab import publish

pytestmark = pytest.mark.skipif(os.environ.get("NIXSCI_INTEGRATION") != "1", reason="opt-in Nix integration")
REPO = Path(__file__).resolve().parents[2]


def cmd(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout.strip()


def flake():
    return f'let f=builtins.getFlake {json.dumps(str(REPO))}; p=f.inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}}; '


def recorded(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"name": "demo", "data": {"loss": {
        "columns": {"epoch": "int", "value": "float"}, "key": ["epoch"]}}}))
    for i in range(3):
        with lab.Run(tmp_path / "runs", f"cache-{tmp_path.name}-{i}", spec=spec, seed=i) as run:
            run.record("loss", {"epoch": 1, "value": 2.0})
        publish.publish(run.directory, project)
    return project, publish.read_lock(project)["demo"]


def test_unsigned_cache_substitutes_exact_fixed_output_path(tmp_path, monkeypatch):
    project, entries = recorded(tmp_path, monkeypatch)
    e = entries[0]
    expected = cmd("nix", "eval", "--impure", "--raw", "--expr", flake() +
                   f'in (f.lib.fetchRun p {{name={json.dumps(e["name"])};sha256={json.dumps(e["sha256"])};}}).outPath')
    assert expected == e["path"]
    cache = "file://" + str(tmp_path / "cache")
    cmd("nix", "copy", "--to", cache, expected)
    assert "Sig:" not in next((tmp_path / "cache").glob("*.narinfo")).read_text()
    expr = ('derivation { name=' + json.dumps(e["name"]) + ';system=builtins.currentSystem;'
            'builder="/bin/sh";args=["-c" "exit 99"];outputHashMode="recursive";'
            'outputHashAlgo="sha256";outputHash=' + json.dumps(e["sha256"]) + ';}')
    store = tmp_path / "empty-store"
    try:
        got = cmd("nix", "build", "--impure", "--store", str(store), "--substituters", cache,
                  "--no-link", "--print-out-paths", "--expr", expr)
        assert got == expected  # builder exits 99: success can only be substitution
    finally:
        if store.exists():
            subprocess.run(["chmod", "-R", "u+w", str(store)], check=True)


def test_sandbox_r_python_bindings_and_claim_report(tmp_path, monkeypatch):
    project, entries = recorded(tmp_path, monkeypatch)
    py = tmp_path / "pipeline.py"
    py.write_text('from nixsci.lab.analysis import use, values\n'
                  'rows=use("demo").table("loss").to_table().to_pylist()\n'
                  'values({"mean":sum(x["value"] for x in rows)/len(rows)})\n')
    r = tmp_path / "pipeline.R"
    r.write_text('library(nixsci);library(dplyr)\n'
                 'x <- use("demo")$loss |> collect()\n'
                 'values(list(mean=mean(x$value)),name="r-values.json")\n')
    expr = flake() + f'l=f.lib.lab {{pkgs=p;project={project};}}; in l.analysis {{'
    expr += (f'name="bindings-smoke";use.demo=l.experiment {{name="demo";}};draft=true;'
             f'pipelines.py.script={py};pipelines.r.script={r};'
             'claims.mean={kind="above";of={use="demo";dataset="loss";column="value";};than=1.0;};confidence=0.99;}')
    result = Path(cmd("nix", "build", "--impure", "--no-link", "--print-out-paths", "--expr", expr))
    assert json.loads((result / "values.json").read_text()) == {"mean": 2.0}
    assert json.loads((result / "r-values.json").read_text()) == {"mean": 2.0}
    claim = json.loads((result / "claims-report.json").read_text())["claims"]["mean"]["original"]
    assert claim["verdict"] == "supported" and claim["level"] == 0.99
