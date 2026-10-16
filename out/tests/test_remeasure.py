import json
from pathlib import Path

from nixsci.out import remeasure


def test_build_plan_freezes_flake(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["nix", "flake", "metadata"]:
            return json.dumps({"path": "/nix/store/frozen-source", "resolvedUrl": "github:x/y"})
        if any("currentSystem" in arg for arg in command):
            return "x86_64-linux"
        return str(tmp_path / "plan.json")

    monkeypatch.setattr(remeasure, "_run", run)
    _, frozen = remeasure._build_plan(".", "paper")
    assert frozen == "/nix/store/frozen-source"
    assert calls[-1][-1] == "/nix/store/frozen-source#packages.x86_64-linux.paper.measurementPlan"


def test_closure_args_are_separate_option_and_value_tokens():
    assert remeasure._closure_args({"x86_64-linux": "/nix/store/code"}) == [
        "--closure", "x86_64-linux=/nix/store/code"
    ]


def test_invalid_level_and_nonempty_output_refused(tmp_path):
    output = tmp_path / "old"
    output.mkdir()
    (output / "paper.pdf").write_bytes(b"old success")
    try:
        remeasure.main(["flake#paper", "--output", str(output), "--level", "0.95"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("nonempty output must be refused")
    try:
        remeasure.main(["flake#paper", "--output", str(tmp_path / "empty"), "--level", "1"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("level 1 must be refused")

