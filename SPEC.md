# Record format v1

The contract between an experiment (any language) and `nix-lab`. It is a
directory of files, so a program on a node with no network and no database can
write it and the runner can `fetch` it later.

## Environment (set by `mkExperiment`'s wrapper and `nix-lab run`)

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
{"v":1,"id":"4121-0","time":"2026-10-04T08:00:00.123456Z","name":"loss",
 "kind":"value","data":0.25,"tags":{"epoch":3}}
{"v":1,"id":"4121-1","time":"...","name":"weights","kind":"artifact",
 "sha256":"…","bytes":4096,"media":"application/x-npy","tags":{}}
```

* `v` is the format version; readers MUST ignore unknown fields.
* `kind` is `value` (inline JSON in `data`) or `artifact` (blob in
  `artifacts/<sha256>`, hash is SHA-256 of the blob bytes).
* `time` is UTC, ISO 8601, microseconds, `Z`.
* Writers append one complete line per `write(2)` with `O_APPEND`, so
  concurrent processes on one node do not interleave.
* Values are JSON only. No pickle, no language-specific encodings: anything
  else is stored as an artifact with a `media` type.

## `status.json`

`{"exit_code": 0, "started": "...Z", "ended": "...Z"}`. A run with no
`status.json` did not exit normally (killed, node lost) and is `incomplete`.

## Adding a language

Implement `record(name, value, **tags)` and artifact storage as above, then
run `tests/conformance.sh <your-cli>`-style fixtures (`tests/fixtures/`).
Nothing else in the system needs to change.
