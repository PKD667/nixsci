"""`nixsci validate`: build each verdict, print the table, exit 1 unless every check holds.

`--rebuild` builds each verdict again and compares the bytes (Nix's `--rebuild`). A difference
means the subject is not deterministic, so the check belongs in lab.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import table

NONDETERMINISTIC = "may not be deterministic"


def _nix(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["nix", *args], text=True, capture_output=True)


def _fail(result: subprocess.CompletedProcess) -> int:
    print(result.stderr, end="", file=sys.stderr)
    return 2


def _attr(flake: str, system: str, name: str) -> str:
    return f"{flake}#validate.{system}.verdicts.{name}"


def _build(flake: str, system: str, name: str) -> tuple[str | None, str]:
    r = _nix("build", "--no-link", "--print-out-paths", _attr(flake, system, name))
    if r.returncode:
        return None, r.stderr
    return r.stdout.strip().splitlines()[-1], ""


def _rebuild(flake: str, system: str, names: list[str]) -> int:
    bad = False
    for name in names:
        built = _nix("build", "--no-link", _attr(flake, system, name))
        if built.returncode:
            print(built.stderr, end="", file=sys.stderr)
            bad = True
            continue
        r = _nix("build", "--no-link", "--rebuild", _attr(flake, system, name))
        if r.returncode == 0:
            print(f"{name}: rebuilt, identical")
            continue
        print(r.stderr, end="", file=sys.stderr)
        bad = True
        if NONDETERMINISTIC in r.stderr:
            print(f"{name}: not deterministic: a rebuild differs from the stored verdict; this check belongs in lab")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nixsci validate")
    ap.add_argument("--flake", default=".", metavar="REF", help="flake that has validate.<system> (default: .)")
    ap.add_argument("--rebuild", action="store_true", help="build each verdict again and compare its bytes")
    ap.add_argument("names", nargs="*", metavar="NAME", help="checks (default: all in validate.<system>.verdicts)")
    a = ap.parse_args(argv)

    sysr = _nix("eval", "--impure", "--raw", "--expr", "builtins.currentSystem")
    if sysr.returncode:
        return _fail(sysr)
    system = sysr.stdout.strip()
    names = a.names
    if not names:
        listed = _nix("eval", "--json", "--apply", "builtins.attrNames", f"{a.flake}#validate.{system}.verdicts")
        if listed.returncode:
            return _fail(listed)
        names = json.loads(listed.stdout)
    if a.rebuild:
        return _rebuild(a.flake, system, names)

    bad = False
    verdicts = []
    for name in names:
        path, err = _build(a.flake, system, name)
        if path is None:
            print(err, end="", file=sys.stderr)
            bad = True
            continue
        verdicts.append(json.loads((Path(path) / "verdict.json").read_text()))
    print(table.markdown(verdicts))
    bad = bad or any(not v["outcome"]["holds"] for v in verdicts)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
