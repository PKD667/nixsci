# Record format v1

The contract between an experiment (any language) and `nixsci.lab`. It is a
directory of files, so a program on a node with no network and no database can
write it and the runner can `fetch` it later.

## Environment (set by `mkExperiment`'s wrapper and the executor)

| variable          | meaning                                                      |
|-------------------|--------------------------------------------------------------|
| `NIX_LAB_DIR`     | directory the program writes records into (created for you)  |
| `NIX_LAB_RUN`     | run id, unique per (spec, params, seed, time)                |
| `NIX_LAB_SEED`    | seed for this run (string, may be unset)                     |
| `NIX_LAB_PARAMS`  | JSON object: this run's parameters                           |
| `NIX_LAB_SCHEMA`  | JSON `{dataset: {column: type}}`; when set, JSON values must be declared rows |
| `NIX_LAB_KEYS`    | JSON `{dataset: [key columns]}`; a key may appear once per run |

## Layout of `$NIX_LAB_DIR`

```
records.jsonl          append-only, one JSON object per line
artifacts/<sha256>     content-addressed blobs referenced by records
status.json            written by the wrapper when the program exits
```

## `records.jsonl`

```json
{"id":"4121-0","time":"2026-10-04T08:00:00.123456Z","name":"loss",
 "kind":"value","data":0.25,"tags":{"epoch":3}}
{"id":"4121-1","time":"...","name":"weights","kind":"artifact",
 "sha256":"…","bytes":4096,"media":"application/x-npy","tags":{}}
```

* Readers MUST ignore unknown fields.
* `kind` is `value` (inline JSON in `data`) or `artifact` (blob in
  `artifacts/<sha256>`, hash is SHA-256 of the blob bytes).
* `time` is UTC, ISO 8601, microseconds, `Z`.
* Writers append one complete line per `write(2)` with `O_APPEND`, so
  concurrent processes on one node do not interleave.
* Values are JSON only. No pickle, no language-specific encodings: anything
  else is stored as an artifact with a `media` type.

## `machine.json`

Written by the `mkExperiment` wrapper on the host that ran the program, next to `status.json`:
`{"hostname", "kernel", "arch", "cpus", "cpu", "mem_kb"}`. A reader comparing two
measurements needs these; a run recorded by hand (`lab.Run`) gets the same facts from Python.

## `status.json`

`{"exit_code": 0, "started": "...Z", "ended": "...Z"}`. A run with no
`status.json` did not exit normally (killed, node lost) and is `incomplete`.

## `manifest.json`

One per run directory, `<runs root>/<app>/<run>/manifest.json`. The executor and `lab.Run`
write it; compaction and `lab.runs` read it. Readers MUST ignore unknown fields.

```json
{"app": "demo", "run": "demo-20261005T061418Z-000-s0", "state": "ok",
 "seed": 0, "params": {"epsilon": 0.1},
 "schema": {"loss": {"epoch": "int", "value": "float"}}, "keys": {"loss": ["epoch"]},
 "started": "2026-10-05T06:14:18Z", "ended": "2026-10-05T06:14:20Z"}
```

Since format 1.1 a manifest also carries: `input_id` and `replicate` (run identity, below),
`records_sha256` (SHA-256 of `records.jsonl`, the seal), `spec_sha256` (and a copy of the spec as
`spec.json` in the run directory), `tolerance` (`{dataset: {column: relative tolerance}}` for noisy
columns), and `machine` (see `machine.json` below). `source` is
`{flake, snapshot, locked, origin}`: `origin` is the flake as locked (rev, narHash), the part a
later reader can rebuild the code from; `locked` describes the local store snapshot.

`input_id` = SHA-256 of the canonical JSON of {app, closure store path, params, seed, schema,
keys}. Runs sharing it are replicates of one measurement; `replicate` numbers them 1, 2, ...
in the order they were made. The runner names the directory `<app>-<input_id[:12]>-r<replicate>`.

`state` is `ok`, `failed` or `incomplete` (no `status.json`, no manifest end); only `ok` runs
are compacted. `schema` and `keys` are the declared datasets the run was recorded under.
The executor adds `status`, `target`, `source`, `closure`, `spec`. Records live in `lab/`
under the run directory when fetched from a remote host, or directly in it for `lab.Run`
(`compact` looks in both).

## Adding a language

Implement `record(name, value, **tags)` and artifact storage as above, then
run `tests/conformance.sh <your-cli>`-style fixtures (`tests/fixtures/`).
Nothing else in the system needs to change.
