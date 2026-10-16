"""Claims over an analysis view, judged on an original and a reproduction.

A claim file is JSON, written by `lab.analysis { claims = ...; }` or by hand:

    { "speedup":  { "kind": "above",   "of": SEL, "than": 1.5 },
      "faster":   { "kind": "greater", "of": SEL, "than": SEL },
      "loss":     { "kind": "near",    "of": SEL, "value": 0.25, "margin": 0.01 },
      "range":    { "kind": "between", "of": SEL, "lo": 0.2, "hi": 0.3 },
      "solved":   { "kind": "exact",   "of": SEL, "value": true } }

    SEL = { "use": "demo", "dataset": "loss", "column": "value",
            "where": { "epoch": 3 }, "stat": "mean" }

`kind` is above, below, between, near (`margin` is absolute, or {"relative": r} of |value|),
greater or less (two selections: the difference of their statistics against 0, or against `by`),
or exact (every sample equals `value`; for columns that do not vary). `stat` is mean (default),
median or proportion (a bool column).

The sample unit defaults to `run`: average matching rows per run before computing an interval.
For proportions, this gives one proportion per run and a t interval across runs. `unit = "row"`
requires `independent = true`: the author asserts rows are independent samples. Only then do bool
rows get Wilson/Newcombe intervals. Exact claims compare every raw selected value.

`level` applies separately to each claim; there is no correction for multiple claims. A report
that supports every claim at 0.95 is not a joint 0.95 confidence statement.

    python -m nixsci.stats.claims CLAIMS --view VIEW [--reproduction VIEW] [--level 0.95] [--json OUT]

Exit status: 0 when every claim is supported (and reproduced, given a reproduction), 1 when one is
contradicted or not reproduced, 3 when the only problem is inconclusive verdicts.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from nixsci import stats

KINDS = ("above", "below", "between", "near", "greater", "less", "exact")


def samples(view: str | Path, sel: dict[str, Any], *, raw: bool = False) -> list:
    """The samples a selection names in a view, in run order."""
    import pyarrow.parquet as pq

    stat = sel.get("stat", "mean")
    if stat not in stats.STATS:
        raise ValueError(f"stat {stat!r}; one of {stats.STATS}")
    directory = Path(view) / sel["use"] / sel["dataset"]
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise ValueError(f"no table {sel['use']}/{sel['dataset']} in {view}")
    where = sel.get("where") or {}
    column = sel["column"]
    per_run: dict[str, list] = {}
    for f in files:
        table = pq.read_table(f).to_pylist()
        for row in table:
            if column not in row:
                raise ValueError(f"{sel['use']}/{sel['dataset']} has no column {column!r}")
            missing = [k for k in where if k not in row]
            if missing:
                raise ValueError(f"{sel['use']}/{sel['dataset']} has no column(s) {missing} for `where`")
            if all(row[k] == v for k, v in where.items()) and (raw or row[column] is not None):
                per_run.setdefault(row["run"], []).append(row[column])
    if not per_run:
        raise ValueError(f"selection {json.dumps(sel, sort_keys=True)} matches no row")
    unit = sel.get("unit", "run")
    if unit not in ("run", "row"):
        raise ValueError("unit must be run or row")
    if not raw and unit == "row" and sel.get("independent") is not True:
        raise ValueError("unit=row requires independent=true; otherwise use one sample per run")
    if stat == "proportion" and any(type(x) is not bool for xs in per_run.values() for x in xs):
        raise ValueError("proportion requires a bool column")
    if raw or unit == "row":
        return [x for run in sorted(per_run) for x in per_run[run]]
    out = []
    for run in sorted(per_run):
        xs = per_run[run]
        out.append(xs[0] if len(xs) == 1 else math.fsum(float(x) for x in xs) / len(xs))
    return out


def evaluate(claim: dict[str, Any], view: str | Path, level: float) -> dict[str, Any]:
    """One claim on one view -> {verdict, estimate, lo, hi, level, n, method}."""
    kind = claim.get("kind")
    if kind not in KINDS:
        raise ValueError(f"claim kind {kind!r}; one of {KINDS}")
    level = float(level)
    stats._check_level(level)
    sel = claim["of"]
    declared_stat = sel.get("stat", "mean")
    stat = "mean" if declared_stat == "proportion" and sel.get("unit", "run") == "run" else declared_stat
    xs = samples(view, sel, raw=kind == "exact")
    if kind == "exact":
        verdict = stats.exact(xs, claim["value"])
        return {"verdict": verdict, "n": len(xs), "values": sorted({json.dumps(x) for x in xs})}
    if kind in ("greater", "less"):
        other = claim["than"]
        other_stat = other.get("stat", "mean")
        other_stat = "mean" if other_stat == "proportion" and other.get("unit", "run") == "run" else other_stat
        if other_stat != stat:
            raise ValueError("greater/less: both selections need the same stat")
        b = stats.difference_bounds(xs, samples(view, other), stat, level)
        by = float(claim.get("by", 0.0))
        verdict = stats.above(b, by) if kind == "greater" else stats.below(b, -by)
        return {"verdict": verdict, "difference": True, **b.as_dict()}
    b = stats.bounds(xs, stat, level)
    if kind == "above":
        verdict = stats.above(b, float(claim["than"]))
    elif kind == "below":
        verdict = stats.below(b, float(claim["than"]))
    elif kind == "between":
        verdict = stats.between(b, float(claim["lo"]), float(claim["hi"]))
    else:  # near
        value, margin = float(claim["value"]), claim["margin"]
        m = abs(value) * float(margin["relative"]) if isinstance(margin, dict) else float(margin)
        verdict = stats.between(b, value - m, value + m)
    return {"verdict": verdict, **b.as_dict()}


def judge(claims: dict[str, Any], view: str | Path, reproduction: str | Path | None, level: float) -> dict[str, Any]:
    stats._check_level(level)
    if not isinstance(claims, dict) or not claims:
        raise ValueError("declare at least one claim")
    report: dict[str, Any] = {"level": level, "scope": "per-claim; no multiplicity correction", "formalVerification": "not-performed", "claims": {}}
    for name in sorted(claims):
        entry: dict[str, Any] = {"claim": claims[name], "original": evaluate(claims[name], view, level)}
        if reproduction is not None:
            entry["reproduction"] = evaluate(claims[name], reproduction, level)
            entry["reproduced"] = stats.reproduced(entry["original"]["verdict"], entry["reproduction"]["verdict"])
        report["claims"][name] = entry
    return report


def status(report: dict[str, Any]) -> int:
    worst = 0
    for entry in report["claims"].values():
        outcome = entry.get("reproduced") or entry["original"]["verdict"]
        if outcome in ("contradicted", "no", "unsupported"):
            return 1
        if outcome == "inconclusive":
            worst = 3
    return worst


def _fmt(r: dict[str, Any]) -> str:
    if "values" in r:
        return f"{r['verdict']:<12} n={r['n']} values={','.join(r['values'])}"
    def num(x: float | None) -> str:
        return "unbounded" if x is None else f"{x:.6g}"

    return (f"{r['verdict']:<12} {'diff ' if r.get('difference') else ''}{r['estimate']:.6g} "
            f"[{num(r['lo'])}, {num(r['hi'])}] n={r['n']} {r['method']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nixsci lab judge", description=__doc__.split("\n\n")[0])
    ap.add_argument("claims", type=Path)
    ap.add_argument("--view", required=True, type=Path, help="the original analysis view")
    ap.add_argument("--reproduction", type=Path, help="the view of a re-measurement")
    ap.add_argument("--level", type=float, default=0.95, help="confidence of each verdict (default 0.95)")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)
    report = judge(json.loads(args.claims.read_text()), args.view, args.reproduction, args.level)
    for name, entry in report["claims"].items():
        print(f"{name}  (level {report['level']})")
        print(f"  original      {_fmt(entry['original'])}")
        if "reproduction" in entry:
            print(f"  reproduction  {_fmt(entry['reproduction'])}")
            print(f"  reproduced    {entry['reproduced']}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return status(report)


if __name__ == "__main__":
    sys.exit(main())
