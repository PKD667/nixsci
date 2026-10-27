"""Turn a check's outcome.json into verdict.json. Runs once, inside the verdict derivation.

The checker writes $out/outcome.json. This step checks its shape and the rules that tie `holds`
to the counterexamples, then writes the verdict with the scope the check declared. It does not
run the checker and cannot tell whether the checker is right: that is the checker's pinned code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

OUTCOME_KEYS = {"holds", "gaps", "counterexamples"}
GAP_KEYS = {"id", "why"}
COUNTEREXAMPLE_KEYS = {"id", "artifact"}


class Refused(Exception):
    """The checker's output breaks a rule. No verdict is written."""


def _item(item, keys: set, where: str) -> dict:
    if not isinstance(item, dict) or set(item) != keys:
        raise Refused(f"outcome.{where} must be an object with exactly the keys {sorted(keys)}")
    for key in sorted(keys):
        if not isinstance(item[key], str) or not item[key]:
            raise Refused(f"outcome.{where}.{key} must be a non-empty string")
    return {key: item[key] for key in sorted(keys)}


def _artifact(out: Path, rel: str, where: str) -> None:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts:
        raise Refused(f"outcome.{where}: artifact {rel!r} must be a relative path inside $out")
    target = out / path
    if not target.exists():
        raise Refused(f"outcome.{where}: artifact {rel!r} does not exist under $out")
    if not target.resolve().is_relative_to(out.resolve()):
        raise Refused(f"outcome.{where}: artifact {rel!r} resolves outside $out")


def judge(meta: dict, out: Path, subject: str, checker: str) -> dict:
    if (out / "verdict.json").exists():
        raise Refused("verdict.json already exists in $out: the command must not write it")
    if not (out / "outcome.json").is_file():
        raise Refused("no outcome.json in $out: the command must write $out/outcome.json")
    try:
        raw = json.loads((out / "outcome.json").read_text())
    except json.JSONDecodeError as error:
        raise Refused(f"outcome.json is not JSON: {error}")
    if not isinstance(raw, dict) or set(raw) != OUTCOME_KEYS:
        found = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise Refused(f"outcome.json must be an object with exactly the keys {sorted(OUTCOME_KEYS)}; found {found}")
    holds = raw["holds"]
    if not isinstance(holds, bool):
        raise Refused("outcome.holds must be true or false")
    if not isinstance(raw["gaps"], list) or not isinstance(raw["counterexamples"], list):
        raise Refused("outcome.gaps and outcome.counterexamples must be lists")
    gaps = [_item(g, GAP_KEYS, f"gaps[{i}]") for i, g in enumerate(raw["gaps"])]
    counters = [_item(c, COUNTEREXAMPLE_KEYS, f"counterexamples[{i}]") for i, c in enumerate(raw["counterexamples"])]
    for i, c in enumerate(counters):
        _artifact(out, c["artifact"], f"counterexamples[{i}]")
    if holds and counters:
        raise Refused("outcome.holds is true but counterexamples is not empty")
    if not holds and not counters:
        raise Refused("outcome.holds is false but counterexamples is empty: a failure names its counterexample")
    return {
        "name": meta["name"],
        "property": meta["property"],
        "model": meta["model"],
        "subject": subject,
        "checker": checker,
        "scope": meta["scope"],
        "outcome": {"holds": holds, "gaps": gaps, "counterexamples": counters},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nixsci.validate.judge")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--checker", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    try:
        verdict = judge(json.loads(Path(a.meta).read_text()), out, a.subject, a.checker)
    except Refused as error:
        print(f"nixsci validate: {error}", file=sys.stderr)
        return 1
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
