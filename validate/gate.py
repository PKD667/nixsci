"""Fail unless a verdict holds. The `holds` derivation runs this on verdict.json.

Prints the property, the scope and every counterexample (id and path) when it does not hold.
Gaps are printed too: they are limits of the checker and never change the outcome.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .table import scope_text


def main(argv=None) -> int:
    path = Path((sys.argv[1:] if argv is None else argv)[0])
    v = json.loads(path.read_text())
    o = v["outcome"]
    if o["holds"]:
        print(f"holds: {v['name']}")
        return 0
    lines = [
        f"does not hold: {v['name']}",
        f"property: {v['property']}",
        f"model: {v['model']}",
        f"scope: {scope_text(v['scope'])}",
    ]
    lines += [f"counterexample {c['id']}: {path.parent / c['artifact']}" for c in o["counterexamples"]]
    lines += [f"gap {g['id']}: {g['why']}" for g in o["gaps"]]
    print("\n".join(lines), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
