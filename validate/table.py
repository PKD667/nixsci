"""The verdict table: one row per check. Used by the `table` derivation and by `nixsci validate`.

`python -m nixsci.validate.table OUTDIR VERDICT.json...` writes OUTDIR/verdicts.json (name ->
verdict) and OUTDIR/verdicts.md (the table).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HEADER = "| name | property | scope | holds | gaps | counterexamples |"


def scope_text(scope: dict) -> str:
    return ", ".join(f"{k}={json.dumps(v, separators=(',', ':'))}" for k, v in sorted(scope.items()))


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def row(v: dict) -> str:
    o = v["outcome"]
    gaps = "; ".join(f"{g['id']}: {g['why']}" for g in o["gaps"]) or "-"
    counters = ", ".join(c["id"] for c in o["counterexamples"]) or "-"
    cells = [v["name"], v["property"], scope_text(v["scope"]), "yes" if o["holds"] else "no", gaps, counters]
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def markdown(verdicts) -> str:
    rows = [row(v) for v in sorted(verdicts, key=lambda v: v["name"])]
    return "\n".join([HEADER, "|" + "---|" * 6, *rows])


def main(argv=None) -> int:
    out, *paths = sys.argv[1:] if argv is None else argv
    verdicts: dict[str, dict] = {}
    for path in paths:
        v = json.loads(Path(path).read_text())
        if v["name"] in verdicts:
            print(f"nixsci validate: two verdicts named {v['name']!r}", file=sys.stderr)
            return 1
        verdicts[v["name"]] = v
    Path(out, "verdicts.json").write_text(json.dumps(verdicts, indent=2, sort_keys=True) + "\n")
    Path(out, "verdicts.md").write_text(markdown(verdicts.values()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
