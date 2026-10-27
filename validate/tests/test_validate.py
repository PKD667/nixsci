"""Verdicts: the judge's rules (no Nix), and the Nix values and CLI on the two examples (needs nix)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nixsci.validate import judge as J
from nixsci.validate import table

REPO = Path(__file__).resolve().parents[2]
META = {"name": "t", "property": "p", "model": "m", "scope": {"n": [1]}}
needs_nix = pytest.mark.skipif(shutil.which("nix") is None, reason="needs nix")


def outdir(tmp_path, outcome=None, raw=None):
    out = tmp_path / "out"
    out.mkdir()
    if raw is not None:
        (out / "outcome.json").write_text(raw)
    elif outcome is not None:
        (out / "outcome.json").write_text(json.dumps(outcome))
    return out


def refused(tmp_path, **kw):
    out = outdir(tmp_path, **kw)
    with pytest.raises(J.Refused) as e:
        J.judge(META, out, "/nix/store/s", "/nix/store/c")
    return str(e.value)


def test_outcome_must_have_exactly_three_keys(tmp_path):
    msg = refused(tmp_path, outcome={"holds": True, "gaps": []})
    assert "exactly the keys" in msg and "counterexamples" in msg


def test_holds_must_be_a_bool(tmp_path):
    assert "true or false" in refused(tmp_path, outcome={"holds": 1, "gaps": [], "counterexamples": []})


def test_a_gap_has_an_id_and_a_why(tmp_path):
    msg = refused(tmp_path, outcome={"holds": True, "gaps": [{"id": "x"}], "counterexamples": []})
    assert "gaps[0]" in msg


def test_holds_true_with_a_counterexample_is_refused(tmp_path):
    out = outdir(tmp_path, {"holds": True, "gaps": [], "counterexamples": [{"id": "c", "artifact": "a"}]})
    (out / "a").write_text("x")
    with pytest.raises(J.Refused, match="holds is true"):
        J.judge(META, out, "s", "c")


def test_holds_false_without_a_counterexample_is_refused(tmp_path):
    assert "counterexamples is empty" in refused(tmp_path, outcome={"holds": False, "gaps": [], "counterexamples": []})


def test_a_counterexample_artifact_must_exist_under_out(tmp_path):
    msg = refused(tmp_path, outcome={"holds": False, "gaps": [], "counterexamples": [{"id": "c", "artifact": "nope"}]})
    assert "does not exist under $out" in msg


def test_an_artifact_outside_out_is_refused(tmp_path):
    msg = refused(tmp_path, outcome={"holds": False, "gaps": [], "counterexamples": [{"id": "c", "artifact": "../x"}]})
    assert "relative path inside $out" in msg


def test_invalid_json_is_refused(tmp_path):
    assert "not JSON" in refused(tmp_path, raw="{")


def test_a_verdict_is_written_with_sorted_keys_and_no_timestamp(tmp_path):
    out = outdir(tmp_path, {"holds": True, "gaps": [{"id": "g", "why": "w"}], "counterexamples": []})
    v = J.judge(META, out, "/s", "/c")
    assert v["outcome"]["gaps"] == [{"id": "g", "why": "w"}] and v["subject"] == "/s"
    assert J.main(["--meta", _write(tmp_path, META), "--out", str(out), "--subject", "/s", "--checker", "/c"]) == 0
    text = (out / "verdict.json").read_text()
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def _write(tmp_path, meta):
    p = tmp_path / "meta.json"
    p.write_text(json.dumps(meta))
    return str(p)


def test_the_table_row_shows_scope_gaps_and_counterexamples():
    v = {"name": "b", "property": "p|q", "scope": {"n": [1, 2]}, "outcome": {
        "holds": False, "gaps": [{"id": "g", "why": "w"}], "counterexamples": [{"id": "c1", "artifact": "a"}]}}
    row = table.row(v)
    assert row == "| b | p\\|q | n=[1,2] | no | g: w | c1 |"


def _nix_expr(command: str, subject_src: str = "let x = 1; in x", extra: str = "") -> str:
    return f"""
let f = builtins.getFlake "path:{REPO}";
    pkgs = f.inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}};
    v = f.lib.validate {{ inherit pkgs; }};
    c = v.check {{
      name = "t";
      property = "p"; model = "m";
      subject = pkgs.writeText "subject.py" ''{subject_src}'';
      checker = pkgs.coreutils;
      scope.n = [ 1 ];
      command = ''{command}'';
      {extra}
    }};
in c"""


def _build(expr: str, attr: str = "verdict", *extra: str):
    return subprocess.run(["nix", "build", "--impure", "--no-link", "--print-out-paths", *extra,
                           "--expr", f"({expr}).{attr}"], text=True, capture_output=True)


@needs_nix
def test_a_malformed_outcome_fails_the_verdict_build_with_the_message():
    r = _build(_nix_expr('echo "{}" > $out/outcome.json'))
    assert r.returncode != 0
    assert "nixsci validate: outcome.json must be an object" in r.stderr


@needs_nix
def test_a_command_that_fails_gives_no_verdict():
    r = _build(_nix_expr("exit 3"))
    assert r.returncode != 0 and "verdict.json" not in r.stderr


@needs_nix
def test_a_check_missing_its_property_is_an_evaluation_error():
    r = _build(_nix_expr("true").replace('property = "p";', 'property = "";'))
    assert r.returncode != 0 and "property must be a non-empty string" in r.stderr


def _example(attr: str, *extra: str):
    return subprocess.run(["nix", "build", "--no-link", "--print-out-paths", *extra,
                           f"path:{REPO}#validate.{_system()}.{attr}"], text=True, capture_output=True)


def _system() -> str:
    return subprocess.run(["nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"],
                          text=True, capture_output=True, check=True).stdout.strip()


@needs_nix
def test_broken_builds_with_holds_false_and_a_counterexample_and_its_gate_fails():
    r = _example("verdicts.broken")
    assert r.returncode == 0, r.stderr
    path = Path(r.stdout.strip().splitlines()[-1])
    v = json.loads((path / "verdict.json").read_text())
    assert v["outcome"]["holds"] is False
    cx = v["outcome"]["counterexamples"][0]
    assert (path / cx["artifact"]).is_file()
    gate = _example("checks.broken")
    assert gate.returncode != 0
    assert "does not hold: broken" in gate.stderr or "does not hold: broken" in (gate.stdout or "")


@needs_nix
def test_sorted_holds_and_rebuilds_identically():
    assert _example("checks.sorted").returncode == 0
    r = _example("verdicts.sorted", "--rebuild")
    assert r.returncode == 0, r.stderr


@needs_nix
def test_cli_table_and_exit_codes_on_the_two_examples():
    def cli(*args):
        return subprocess.run([sys.executable, "-m", "nixsci.validate.cli", "--flake", f"path:{REPO}", *args],
                              text=True, capture_output=True)
    ok = cli("sorted")
    assert ok.returncode == 0 and "| sorted |" in ok.stdout and "| yes |" in ok.stdout
    bad = cli("broken")
    assert bad.returncode == 1 and "| no |" in bad.stdout and "n2-0" in bad.stdout
    both = cli()
    assert both.returncode == 1 and "| broken |" in both.stdout and "| sorted |" in both.stdout

