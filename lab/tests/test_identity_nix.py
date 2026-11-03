"""The identity of a run, computed twice: by the executor in Python (store.input_id) and by lab.nix, to
tell a locked run of the experiment as it is now from one that is not. They must agree to the byte, so
the checks here evaluate real Nix and compare. They need nix, so the sandboxed flake checks leave them
out (flake.nix) and `nix develop --command pytest` runs them.
"""
import json
import subprocess
from pathlib import Path

import pytest

from nixsci.lab import publish, store
from nixsci.lab import spec as spec_mod

REPO = Path(__file__).resolve().parents[2]
CLOSURE = "/nix/store/00000000000000000000000000000000-experiment-demo"
SHA = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
COLUMNS = {"epoch": "int", "value": "float"}


def nix(expression):
    done = subprocess.run(["nix", "eval", "--impure", "--json", "--expr", expression],
                          text=True, capture_output=True, check=True)
    return json.loads(done.stdout)


def preamble():
    return (f'let f = builtins.getFlake {json.dumps(str(REPO))}; p = f.inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}}; '
            f'lab = locked: import {REPO}/lab/nix/lab.nix {{ pkgs = p; project = /nonexistent; nixsci = null; python = null; rEnv = null; lockedOverride = locked; }}; ')


def experiment(args, closure=CLOSURE):
    """The experiment value Nix builds from `args` (a JSON-able dict), with one closure."""
    return f'(lab null).experiment ({{ name = "demo"; closures.${{builtins.currentSystem}} = {json.dumps(closure)}; }} // builtins.fromJSON {json.dumps(json.dumps(args))})'


CASES = [
    {"seeds": [0, 1], "sweep": {"epsilon": [0.1, 0.3]}, "params": {"size": 32},
     "data": {"loss": {"columns": COLUMNS, "key": ["epoch"], "noisy": {"value": 0.25}}}},
    {},  # no seeds: the seed is null
    {"seeds": [3], "params": {"lr": 1e-07, "scale": 1.0, "big": 123456789.125, "n": 2**40, "on": True, "tags": ["a", "b"], "nest": {"x": None}}},
    {"seeds": [0], "params": {"name": "café", "label": "日本語 \"quoted\" \\ back"}},
    {"seeds": [0], "span": 4, "data": {"loss": {"columns": {**COLUMNS, "split": "str?"}}}},
    {"seeds": [0], "span": 1},
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: ",".join(sorted(c)) or "empty")
def test_nix_computes_the_ids_python_writes_into_the_lock(case):
    built = nix(preamble() + f'e = {experiment(case)}; in {{ spec = e.spec; ids = e.ids; }}')
    spec = spec_mod.from_json(built["spec"])
    assert sorted(built["ids"]) == sorted(store.current_ids(spec, [CLOSURE]))


def locked_run(spec, closure=CLOSURE, replicate=1, job=0):
    job = spec.jobs()[job]
    ident = store.job_id(spec, closure, job)
    name = f"demo-{ident[:12]}-r{replicate}"
    manifest = {"format": "2.0", "run": name, "state": "ok", "closure": closure, "seed": job.seed,
                "params": job.params, "input_id": ident, "replicate": replicate}
    return {"name": name, "path": f"/nix/store/x-{name}", "sha256": SHA, "facts": publish.facts(manifest)}


def verdict(entries, args):
    expression = (preamble() + f'e = {experiment(args)}; a = (lab {{ demo = builtins.fromJSON {json.dumps(json.dumps(entries))}; }}).analysis '
                  '{ name = "a"; use.demo = e; pipelines = { }; draft = true; }; '
                  'in { inherit (a) clean stale orphan missing untraced; }')
    return nix(expression)


def spec_of(args):
    return spec_mod.from_json({"kind": "experiment", "name": "demo", **args})


ARGS = {"seeds": [0], "replicates": 2, "data": {"loss": {"columns": COLUMNS}}}


def test_the_runs_of_the_current_experiment_are_clean():
    spec = spec_of(ARGS)
    got = verdict([locked_run(spec, replicate=1), locked_run(spec, replicate=2)], ARGS)
    assert got["clean"] is True


def test_a_run_of_an_older_schema_is_an_orphan_and_does_not_count_toward_replicates():
    old = spec_of({**ARGS, "data": {"loss": {"columns": {"epoch": "int"}}}})
    spec = spec_of(ARGS)
    got = verdict([locked_run(old), locked_run(spec)], ARGS)
    assert got["clean"] is False
    assert got["orphan"] == [f"demo/{locked_run(old)['name']}"]
    assert len(got["missing"]) == 1  # one current run of two


def test_a_run_with_another_span_is_an_orphan():
    spanned = spec_of({**ARGS, "span": 2})
    got = verdict([locked_run(spanned, replicate=1), locked_run(spanned, replicate=2)], ARGS)
    assert len(got["orphan"]) == 2 and got["clean"] is False


def test_a_run_of_another_closure_is_stale_not_orphan():
    spec = spec_of(ARGS)
    got = verdict([locked_run(spec, closure=CLOSURE + "-old")], ARGS)
    assert got["stale"] == [f"demo/{locked_run(spec, closure=CLOSURE + '-old')['name']}"] and got["orphan"] == []
