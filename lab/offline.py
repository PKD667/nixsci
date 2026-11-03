"""The Python behind a lab export: what the export's build and its shell scripts call.

An export is a directory a reviewer runs without Nix (see `lab.export` in lab/nix/lab.nix). Its
Python runs in two places:

    python -m nixsci.lab.offline write      build time (lab/nix/lab.nix, export): jobs and environments
    python -m nixsci.lab.offline paths      build time: which store paths the export ships
    python -m nixsci.lab.offline readme     build time: README.md and evaluation/claims.md
    python -m nixsci.lab.offline machines   build time: original/machines.json
    python -m nixsci.lab.offline seal-run   `run` (lab/export/run): one finished job becomes a run
    python -m nixsci.lab.offline span       `run` (lab/export/run): refuses a multi-host spec
    python -m nixsci.lab.offline tables     `evaluate` (lab/export/evaluate): row counts of a view

Every file this module writes is plain text: a table with a header line, KEY=VALUE lines, or
Markdown. A run it seals is the same kind of directory the executor makes (runner.record).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

from . import store
from . import spec as spec_mod
from .runner import job_env, record
from .spec import Job, Spec

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: What a job gets from the executor's job_env (deploy/backend.py) that is not NIX_LAB_*, and
#: that the export must give too: a job that imports nixsci.lab needs PYTHONPATH from the
#: deployment's env, and the executor never writes bytecode into a store path.
JOB_DEFAULTS = {"PYTHONDONTWRITEBYTECODE": "1"}

#: Verdict wording. The verdicts and their meaning are from stats/__init__.py (its docstring).
VERDICTS = {
    "supported": "the data support the claim at the stated confidence: the whole interval lies "
                 "inside the region the claim names.",
    "contradicted": "the data support the negation of the claim at the stated confidence: the whole "
                    "interval lies outside the region the claim names.",
    "inconclusive": "neither: the interval straddles the boundary, or there are too few samples. "
                    "Measure more replicates.",
}
REPRODUCED = {
    "yes": "the original and the reproduction both support the claim.",
    "no": "the original supports the claim, the reproduction contradicts it. This is \"not reproduced\".",
    "inconclusive": "the original supports the claim, the reproduction is inconclusive. Measure more "
                    "replicates.",
    "unsupported": "the original did not support the claim, so there is nothing to reproduce.",
}


def job_lines(spec: Spec, closure: str) -> list[dict[str, Any]]:
    """One line per run the export will make: the executor's jobs, times their replicates.

    `index` numbers the lines: `./run 3` runs line 3. Run ids are the executor's
    (runner.py: `<name>-<input id, 12 characters>-r<replicate>`).
    """
    lines = []
    for job in spec.jobs():
        ident = store.input_id(spec.name, closure, job.params, job.seed, spec.data, spec.keys)
        for replicate in range(1, spec.replicates + 1):
            lines.append({
                "index": len(lines),
                "run_id": f"{spec.name}-{ident[:12]}-r{replicate}",
                "seed": job.seed,
                "params": job.params,
                "input_id": ident,
                "replicate": replicate,
                "job": job,
            })
    return lines


def write(export_dir: str | Path, spec_json: str | Path, closure_path: str, deployment_json: str | Path) -> int:
    """Write experiment/jobs.tsv and experiment/env/<index>.env. Called at build time.

    Returns the number of lines (runs) written. The env file of a line holds every variable the
    job gets that does not depend on the reviewer's machine: the NIX_LAB_* variables, the
    deployment's env entries, and PYTHONDONTWRITEBYTECODE. `run` adds the rest (PATH, HOME, the
    workdir) as plain lines of shell.
    """
    export = Path(export_dir)
    spec = spec_mod.read(spec_json)
    deployment = json.loads(Path(deployment_json).read_text())
    closure_env = deployment.get("env") or {}
    lines = job_lines(spec, closure_path)
    (export / "experiment" / "env").mkdir(parents=True, exist_ok=True)
    table = ["index\trun_id\tseed\tparams"]
    for line in lines:
        job: Job = line["job"]
        table.append("\t".join([
            str(line["index"]),
            line["run_id"],
            "" if job.seed is None else str(job.seed),
            json.dumps(job.params, sort_keys=True),
        ]))
        values = {**JOB_DEFAULTS, **closure_env}
        values.update(job_env(spec, job, line["run_id"]))
        (export / "experiment" / "env" / f"{line['index']}.env").write_text(env_text(values))
    (export / "experiment" / "jobs.tsv").write_text("\n".join(table) + "\n")
    return len(lines)


def env_text(values: Mapping[str, str]) -> str:
    """KEY=VALUE lines, sorted by key. A value with a line break cannot be written this way."""
    out = []
    for key in sorted(values):
        value = str(values[key])
        if not _KEY.fullmatch(key):
            raise ValueError(f"environment name {key!r} is not a shell name")
        if "\n" in value or "\x00" in value:
            raise ValueError(f"environment value of {key} has a line break: not representable as KEY=VALUE")
        out.append(f"{key}={value}\n")
    return "".join(out)


def read_lines(export_dir: str | Path) -> list[dict[str, Any]]:
    """experiment/jobs.tsv, parsed: index, run_id, seed (int or None), params (dict)."""
    rows = (Path(export_dir) / "experiment" / "jobs.tsv").read_text().splitlines()
    head = rows[0].split("\t")
    if head != ["index", "run_id", "seed", "params"]:
        raise ValueError(f"jobs.tsv header is {head}, expected index, run_id, seed, params")
    out = []
    for text in rows[1:]:
        index, run_id, seed, params = text.split("\t")
        out.append({
            "index": int(index),
            "run_id": run_id,
            "seed": None if seed == "" else int(seed),
            "params": json.loads(params),
        })
    return out


def seal_run(export_dir: str | Path, index: int, workdir: str | Path, started: str) -> dict[str, Any]:
    """Seal the job of line `index` that ran in `workdir`; called by `run` after the job exits.

    The run lands in runs/<name>/<run id>/. Its `lab/` directory moves there, so the record is the
    executor's record, and `nixsci lab add` can import it later. A finished job's workdir is
    removed; a failed one stays, as the executor leaves it.
    """
    export = Path(export_dir)
    workdir = Path(workdir)
    spec = spec_mod.read(export / "experiment" / "spec.json")
    line = next(r for r in read_lines(export) if r["index"] == index)
    closure = (export / "experiment" / "closure").read_text().strip()
    source = json.loads((export / "experiment" / "source.json").read_text())
    run_id = line["run_id"]
    ident = store.input_id(spec.name, closure, line["params"], line["seed"], spec.data, spec.keys)
    dest = export / "runs" / spec.name / run_id
    if dest.exists():
        raise FileExistsError(f"{dest} exists: a run is not replaced; delete it to run the job again")
    dest.mkdir(parents=True)
    if (workdir / "lab").is_dir():
        shutil.move(str(workdir / "lab"), str(dest / "lab"))
    status_file = dest / "lab" / "status.json"
    status = json.loads(status_file.read_text()) if status_file.is_file() else None
    job = Job(index, line["seed"], line["params"])
    manifest = record(
        dest, spec, job, ident=ident, replicate=int(run_id.rsplit("-r", 1)[1]), run_id=run_id,
        status=status, target="export", started=started, source=source, closure=closure,
    )
    if manifest["state"] == "ok":
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"{manifest['state']} -> {dest}")
    return manifest


def span(spec_json: str | Path) -> int:
    """The span of a spec: hosts per job. An export runs one host, so `run` refuses span > 1."""
    return spec_mod.read(spec_json).span


def shipped_paths(registration: str | Path, out: str | Path) -> int:
    """nix's closureInfo `registration` -> experiment/paths.tsv: path, NAR hash, NAR size.

    The registration file is nix-store --load-db format: per path, the path, its deriver (empty
    here), its NAR hash and size, then its references. Only the first three are kept.
    """
    lines = Path(registration).read_text().splitlines()
    rows, i = [], 0
    while i < len(lines):
        path, nar_hash, nar_size, _deriver, count = lines[i:i + 5]
        rows.append((path, nar_hash, nar_size))
        i += 5 + int(count)
    text = "path\tnar_hash\tnar_size\n" + "".join(f"{p}\t{h}\t{s}\n" for p, h, s in rows)
    Path(out).write_text(text)
    return len(rows)


def kind_text(claim: Mapping[str, Any]) -> str:
    """A claim's kind in words, with its threshold (stats/claims.py, its docstring)."""
    kind = claim["kind"]
    if kind == "above":
        return f"the quantity is above {claim['than']}"
    if kind == "below":
        return f"the quantity is below {claim['than']}"
    if kind == "between":
        return f"the quantity lies between {claim['lo']} and {claim['hi']}, both included"
    if kind == "near":
        margin = claim["margin"]
        if isinstance(margin, Mapping):
            size = f"{margin['relative']} times the absolute value of {claim['value']}"
        else:
            size = f"{margin}"
        return f"the quantity is within {size} of {claim['value']}"
    if kind in ("greater", "less"):
        by = claim.get("by", 0.0)
        word = "greater" if kind == "greater" else "less"
        return f"the difference between the two selections is {word} than {by}"
    if kind == "exact":
        return f"every selected value equals {json.dumps(claim['value'])}"
    raise ValueError(f"claim kind {kind!r} is unknown")


def selection_text(sel: Mapping[str, Any]) -> str:
    where = sel.get("where") or {}
    rows = ", ".join(f"{k} = {json.dumps(v)}" for k, v in sorted(where.items())) or "all rows"
    stat = sel.get("stat", "mean")
    if sel.get("unit", "run") == "run":
        unit = "one sample per run (rows of a run are averaged first)"
    else:
        unit = "each row is one sample (the author asserts the rows are independent)"
    return (f"dataset `{sel['use']}/{sel['dataset']}`, column `{sel['column']}`, rows where {rows}; "
            f"statistic {stat}; {unit}")


def claims_md(claims: Mapping[str, Any], level: float | None) -> str:
    """evaluation/claims.md: each claim in words, with its selection, kind, threshold and level."""
    if not claims:
        return "# Claims\n\nThis analysis declares no claims. Nothing is judged.\n"
    out = [
        "# Claims",
        "",
        f"Each claim is judged at confidence {level} (the interval coverage). Claims are judged one at "
        "a time: there is no correction for several claims, so a set of supported claims is not a "
        f"joint {level} statement.",
        "",
        "Verdicts, for the original runs and for a reproduction:",
        "",
    ]
    out += [f"- `{name}`: {text}" for name, text in VERDICTS.items()]
    out += ["", "Reproduction, given both verdicts (`reproduced` in claims-report.json):", ""]
    out += [f"- `{name}`: {text}" for name, text in REPRODUCED.items()]
    out.append("")
    for name in sorted(claims):
        claim = claims[name]
        out += [f"## {name}", ""]
        out.append(f"Selection: {selection_text(claim['of'])}.")
        out.append("")
        out.append(f"Claim: {kind_text(claim)}. Kind `{claim['kind']}`, confidence {level}.")
        out.append("")
    return "\n".join(out)


def machines(out: str | Path, run_dirs: list[str]) -> int:
    """original/machines.json: the machine each original run recorded, by run id.

    The analysis view's runs.json does not carry the machine, so the export takes it from each
    locked run's manifest.json (the run's sealed record, not the view).
    """
    found = {}
    for run in run_dirs:
        manifest = json.loads((Path(run) / "manifest.json").read_text())
        found[manifest["run"]] = manifest.get("machine")
    Path(out).write_text(json.dumps(found, sort_keys=True, indent=1) + "\n")
    return len(found)


def level_of(export_dir: str | Path) -> float | None:
    """The confidence the analysis's report was made at, or None when there is no report."""
    report = Path(export_dir) / "original" / "results" / "claims-report.json"
    return float(json.loads(report.read_text())["level"]) if report.is_file() else None


def readme(export_dir: str | Path, spec: Spec, claims: Mapping[str, Any] | None = None, level: float | None = None) -> str:
    """README.md for an export. Deterministic: no dates, no paths of this machine."""
    export = Path(export_dir)
    closure = (export / "experiment" / "closure").read_text().strip()
    system = (export / "experiment" / "system").read_text().strip()
    source = json.loads((export / "experiment" / "source.json").read_text())
    alias = (export / "experiment" / "alias").read_text().strip()
    jobs = read_lines(export)
    paths = (export / "experiment" / "paths.tsv").read_text().splitlines()[1:]
    unpacked = sum(int(line.split("\t")[2]) for line in paths)
    analysis = (export / "evaluation").is_dir()
    lines = [
        f"# {spec.name}: a lab export",
        "",
        "Run these three commands, in this directory, in this order. You do not need to know Nix.",
        "",
        f"1. `./check`: tests this machine. Prints `ok:` for each test that passes and `FAIL:` with the "
        "cause and the one thing to do for each that fails. Prints nothing else.",
        f"2. `./run`: runs the {len(jobs)} jobs one after another and prints each job's output. Its run "
        "is kept in `runs/`. `./run 3` runs only line 3. It stops with `FAIL:` if a job fails.",
        "3. `./evaluate`: rebuilds the tables from your runs, runs the analysis"
        + (", and judges the claims against the original runs. It prints each claim as "
           "`supported`, `contradicted`, `inconclusive` or `reproduced`/`not reproduced`."
           if analysis else ". This export has no analysis, so it prints the tables only."),
        "",
        "Each command exits 0 when it succeeds. Anything else means a `FAIL:` line printed above, and "
        "that line says what to do.",
        "",
        "## What the experiment is",
        "",
        f"- Name `{spec.name}`, system `{system}`, closure `{closure}`.",
        f"- Source: `source/`, the project tree that built the closure, at revision "
        f"`{source.get('rev')}`.",
        f"- Seeds: {', '.join(map(str, spec.seeds)) or 'none'}. Replicates: {spec.replicates}. "
        f"Parameters: {json.dumps(spec.params, sort_keys=True)}. Sweep: {json.dumps(spec.sweep, sort_keys=True)}.",
        f"- Lines in `experiment/jobs.tsv`: {len(jobs)}. Each line is one run. Run ids are the executor's, so "
        "they match the original runs.",
        "- Measured datasets. Each run writes these rows with `lab.record`. Every row also carries `run`, "
        "`seed` and `time`, which nixsci adds.",
        "",
    ]
    for dataset in sorted(spec.data):
        lines.append(f"  - `{dataset}`:")
        for column, kind in spec.data[dataset].items():
            lines.append(f"    - `{column}`: {kind}")
    lines += [
        "",
        "## Files",
        "",
        "- `check`, `run`, `evaluate`: the three commands above, plain POSIX sh.",
        "- `experiment/`: the spec (`spec.json`), the lines to run (`jobs.tsv`), their environments "
        "(`env/`), the deployment (`deployment.json`), the closure, the source identity and the store "
        "paths shipped (`paths.tsv`).",
        "- `source/`: the code that the closure was built from.",
        "- `runs/<name>/<run id>/`: a run you made (`lab/` holds its records, `manifest.json` its seal).",
        "- `reproduction/`: your tables (`view/`), your pipeline outputs (`results/`) and, with an analysis, "
        "`claims-report.json`.",
        "- `tools/`: `nix` (the static binary), `cache/` (the closures) and `store/` (made by `check`).",
    ]
    if analysis:
        lines += [
            "- `evaluation/`: the analysis: `claims.json`, `claims.md` (each claim in words), and the "
            "pipelines (`pipelines/`).",
            "- `original/`: the analysis's view of the original runs (`view/`) and its outputs (`results/`).",
        ]
    lines += ["", "## Nix, for whoever wants to know", ""]
    lines += [
        "Nix is not something you install or configure. It is the one binary shipped in `tools/nix`.",
        "",
        "Nix is shipped as one static binary, `tools/nix`. It does one thing: it copies the closures in "
        "`tools/cache` into `tools/store`, and runs commands inside a user namespace in which "
        "`tools/store` appears as `/nix/store`. Nothing else runs through Nix. Every command a script "
        "runs is a line of shell in the script, and every value a job receives is a line in "
        "`experiment/env/<line>.env` or a line of `run`. The store holds the program the jobs run "
        "(`experiment/closure`), its dependencies, and the evaluator (`tools/evaluator`, and "
        "`tools/r-env` for R pipelines).",
        "",
        "## The experiment",
        "",
        f"- Name `{spec.name}`, system `{system}`, closure `{closure}`.",
        f"- Source: `source/`, the project tree that built the closure, at revision "
        f"`{source.get('rev')}`.",
        f"- Seeds: {', '.join(map(str, spec.seeds)) or 'none'}. Replicates: {spec.replicates}. "
        f"Parameters: {json.dumps(spec.params, sort_keys=True)}. Sweep: {json.dumps(spec.sweep, sort_keys=True)}.",
        f"- Lines in `experiment/jobs.tsv`: {len(jobs)}. Each line is one run. Run ids are the executor's, so "
        "they match the original runs.",
        "- Measured datasets. Each run writes these rows with `lab.record`. Every row also carries `run`, "
        "`seed` and `time`, which nixsci adds.",
        "",
    ]
    for dataset in sorted(spec.data):
        lines.append(f"  - `{dataset}`:")
        for column, kind in spec.data[dataset].items():
            lines.append(f"    - `{column}`: {kind}")
    lines += [
        "",
        "## Files",
        "",
        "- `experiment/`: the spec (`spec.json`), the lines to run (`jobs.tsv`), their environments "
        "(`env/`), the deployment (`deployment.json`), the closure, the source identity and the store "
        "paths shipped (`paths.tsv`).",
        "- `source/`: the code that the closure was built from.",
        "- `runs/<name>/<run id>/`: a run you made (`lab/` holds its records, `manifest.json` its seal).",
        "- `reproduction/`: your tables (`view/`), your pipeline outputs (`results/`) and, with an analysis, "
        "`claims-report.json`.",
        "- `tools/`: `nix` (the static binary), `cache/` (the closures) and `store/` (made by `check`).",
    ]
    if analysis:
        lines += [
            "- `evaluation/`: the analysis: `claims.json`, `claims.md` (each claim in words), and the "
            "pipelines (`pipelines/`).",
            "- `original/`: the analysis's view of the original runs (`view/`) and its outputs (`results/`).",
        ]
    lines += ["", "## Requirements and limits", ""]
    lines += [
        f"- Linux {system.split('-')[0]} with unprivileged user namespaces. `check` tests this.",
        f"- Disk: about {unpacked // 2**20} MiB free for the store (the NAR sizes in `experiment/paths.tsv`).",
        "- Hardware differs. Each run records its machine in `lab/machine.json`. With an analysis, "
        "`original/machines.json` gives the machine of each original run, by run id. A timing that differs "
        "is not a failed reproduction by itself; the claims say which numbers count.",
        "- A job that spans more than one host is refused by `run`: it needs hosts and a launcher this export "
        "does not have.",
        "- Jobs run without a timeout and without retries. A failed run stays in `runs/`; `evaluate` stops at "
        "it, so delete it or fix it first. A run that exists is not run again; delete its directory to run "
        "the line again.",
        "- An analysis that uses more than one experiment is refused at build time: this export ships "
        "one experiment's closure.",
        "- Pipelines run with the packages they declared, put into `tools/evaluator` (and `tools/r-env`).",
        "- A job gets no `NIX_DEPLOY_ENTER`: it runs in the store view and starts no other program through it.",
    ]
    if claims is not None:
        lines += ["", "## Claims", ""]
        if claims:
            lines.append(f"This analysis judges {len(claims)} claim(s) at confidence {level}. "
                         "`evaluation/claims.md` says what each one selects and how it is judged.")
        else:
            lines.append("This analysis declares no claims.")
    return "\n".join(lines) + "\n"


def table_counts(view: str | Path) -> list[tuple[str, int]]:
    """Each table under a view (`<alias>/<table>/*.parquet`) with its row count."""
    import pyarrow.parquet as pq

    out = []
    for table in sorted(p for p in Path(view).iterdir() if p.is_dir()):
        rows = sum(pq.read_metadata(f).num_rows for f in table.glob("*.parquet"))
        out.append((table.name, rows))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nixsci.lab.offline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("export")
    w.add_argument("spec")
    w.add_argument("closure")
    w.add_argument("deployment")
    s = sub.add_parser("seal-run")
    s.add_argument("export")
    s.add_argument("index", type=int)
    s.add_argument("workdir")
    s.add_argument("started")
    r = sub.add_parser("readme", help="write README.md and evaluation/claims.md")
    r.add_argument("export")
    r.add_argument("spec")
    p = sub.add_parser("paths")
    p.add_argument("registration")
    p.add_argument("out")
    sp = sub.add_parser("span")
    sp.add_argument("spec")
    m = sub.add_parser("machines")
    m.add_argument("out")
    m.add_argument("runs", nargs="*")
    t = sub.add_parser("tables")
    t.add_argument("view")
    args = ap.parse_args(argv)

    if args.cmd == "write":
        print(write(args.export, args.spec, args.closure, args.deployment))
    elif args.cmd == "seal-run":
        manifest = seal_run(args.export, args.index, args.workdir, args.started)
        return 0 if manifest["state"] == "ok" else 1
    elif args.cmd == "readme":
        export = Path(args.export)
        spec = spec_mod.read(args.spec)
        claims_file = export / "evaluation" / "claims.json"
        claims, level = None, None
        if claims_file.is_file():
            claims = json.loads(claims_file.read_text())["claims"]
            level = level_of(export)
            if claims and level is None:
                raise SystemExit("claims without a claims report: the analysis was not built")
            (export / "evaluation" / "claims.md").write_text(claims_md(claims, level))
        (export / "README.md").write_text(readme(export, spec, claims, level))
    elif args.cmd == "paths":
        print(shipped_paths(args.registration, args.out))
    elif args.cmd == "span":
        print(span(args.spec))
    elif args.cmd == "machines":
        print(machines(args.out, args.runs))
    elif args.cmd == "tables":
        for name, rows in table_counts(args.view):
            print(f"{name}  {rows} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
