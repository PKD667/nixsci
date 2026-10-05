# nix-lab

Experiment records and run specs. An experiment, in any language, records what
it measured in one line. Runs are described in TOML, expanded into seeds and
parameter sweeps, executed on any host, and their records come back next to a
manifest saying exactly which code produced them.

The record side has no dependencies. Running experiments remotely uses
[nix-deploy](https://github.com/PKD667/nix-deploy), which is an optional extra
(`nix-lab[deploy]`); nix-lab never reimplements deployment.

## Recording (inside an experiment)

```python
import lab

lab.record("loss", 0.25, epoch=3)         # JSON-like value, stored inline
lab.record("weights", numpy_array)        # arrays, bytes and paths become content-addressed artifacts
lab.params()                              # this run's parameters (dict)
lab.seed()                                # this run's seed (int or None)
```

Values are JSON only. There is no pickle: anything else is stored as an artifact
with a media type, so any language can read it back. Records are appended to
`$NIX_LAB_DIR/records.jsonl`; large payloads go to `$NIX_LAB_DIR/artifacts/<sha256>`.
The exact on-disk format is [SPEC.md](SPEC.md). It is the contract: a writer in
another language (Rust, R, OCaml...) only has to produce that format, and nothing
else in the system changes.

Reading back:

```python
import lab
rows = lab.load("runs/blocks/blocks-20261005T...-000-s0")      # records of one run
lab.artifact(run_dir, rows[3])                                 # bytes of an artifact record
lab.runs("runs", app="blocks", seed=1, params__epsilon=0.3)    # find run directories by manifest fields
```

## Describing runs

`experiments/blocks.toml`:

```toml
[experiment]
name  = "blocks"           # also the default flake attribute
flake = "."                # flake with experiments.<system>.<attr>
attr  = "blocks"
seeds = [0, 1]             # one run per seed

[params]                   # fixed parameters, visible via lab.params()
size = 32

[sweep]                    # every combination becomes a run
epsilon = [0.1, 0.3]

[resources]                # generic; same meaning for every provider
provider = "g5k"           # a name from ~/.config/nix-deploy/providers.toml
hosts = 2
walltime = 120             # minutes

[resources.opts]           # provider-specific settings, and only here
queue = "besteffort"
```

```sh
nix-lab plan experiments/blocks.toml          # list the jobs this expands to
nix-lab run  experiments/blocks.toml --out runs
```

`run` resolves the flake once, leases hosts from the provider, spreads the jobs
over them, waits, fetches each run's `lab/` directory and writes
`runs/<name>/<run>/manifest.json` (state `ok` / `failed` / `incomplete`, seed,
params, target, start/end times, flake source identity and closure path).
Unknown tables in the TOML, and provider settings placed outside `[resources.opts]`,
are errors.

## Typed datasets

Declare a dataset's columns in the spec and `lab.record` checks every row against
them, at record time, in the run itself:

```toml
[data.loss]
columns = { epoch = "int", value = "float", split = "str?" }   # ? = may be null
```

```python
lab.record("loss", {"epoch": 3, "value": 0.25, "split": None})   # ok
lab.record("loss", {"epoch": 3.5, "value": 0.25, "split": "a"})  # TypeError, nothing written
lab.record("lossy", {...})                                       # ValueError: not declared
```

Add `key = ["epoch", "split"]` to a dataset and each key may appear once per run: a second
row with the same key is refused at record time, and `compact` re-checks the whole run (a
restarted process would not remember). Key columns must be declared and not nullable.

Types are `int`, `float`, `str`, `bool`. A row must carry exactly the declared
columns (`run`, `seed` and `time` are reserved: nix-lab adds them). Once a spec
declares datasets, JSON values must be rows of a declared dataset; arrays, bytes
and files still record freely as artifacts. The schema travels to the run in
`NIX_LAB_SCHEMA` (keys in `NIX_LAB_KEYS`) and is copied into each run's `manifest.json`.

## Compaction and analysis

```sh
nix-lab compact runs --out data             # needs pyarrow (the nix-lab package has it)
nix-lab analyze experiments/demo.toml --runs runs --data data --out analysis
```

`compact` writes each finished run's declared datasets as typed Parquet,
`data/<app>/<dataset>/<run id>.parquet`, with the declared columns plus `run`,
`seed` and `time`. Runs that did not end `ok` are skipped.

Analysis is R. Declare pipelines next to the data:

```toml
[pipeline.curve]
script = "demo.R"            # path relative to the spec
inputs = ["demo"]            # apps it reads (default: this experiment)
deps = ["helpers.R"]        # other files the script uses (hashed with it)
```

`analyze` runs the script with `Rscript` and these variables: `NIX_LAB_DATA`
(the Parquet root), `NIX_LAB_RUNS` (raw runs), `NIX_LAB_OUT` (`<out>/<pipeline>/`).
The `labr` R package reads them:

```r
library(dplyr); library(labr)
loss <- lab_data("demo", "loss") |> collect()    # lazy Arrow dataset over all runs
runs <- lab_manifests("demo")                    # one row per run, params as param.<name>
write.csv(summary, lab_out("final.csv"))
```

A pipeline whose last run succeeded is skipped (`up to date`) while its script, its `deps`
files and its input runs (with their manifest hashes) are unchanged; `--force` reruns.

After each pipeline a `provenance.json` is written beside its outputs: the script
and its hash, every input run with the hash of its manifest, the R version, the
exit code. A figure or table can therefore say exactly which runs and which code
produced it. For pinned R packages run inside the flake's R environment
(`nix build .#r-env`, then `NIX_LAB_RSCRIPT=<out>/bin/Rscript`, or put it on
`PATH`). `examples/demo.{toml,py,R}` is a complete worked example.

## Recording from Rust

`rust/lab` is a small crate (serde_json + sha2) that writes the same format:

```rust
lab::record("loss", &serde_json::json!({"epoch": 3, "split": "train", "value": 0.25}))?;
lab::record_bytes("weights", &bytes, "application/octet-stream")?;
let epsilon = lab::params()["epsilon"].as_f64();   // lab::seed() -> Option<i64>
```

It enforces the same declared columns and keys as the Python module. The crate lives in
`rust/lab`: use it as a path dependency (`lab = { path = "../nix-lab/rust/lab" }`) or vendor it.
`tests/test_rust.py` builds
nothing itself: point `NIX_LAB_RUST_EMIT` at `cargo build --example emit` and Python checks that
it can read and validate what Rust wrote. A recorder in another language only has to follow
`SPEC.md`.

## Making a flake experiment

`lib.mkExperiment` wraps a program so it gets a record directory, forwards
SIGTERM, and always writes `status.json`:

```nix
experiments.${system}.blocks = nix-lab.lib.mkExperiment pkgs {
  name = "blocks";
  program = "${python}/bin/python ${./blocks.py}";
  runtime = [ pkgs.clang ];           # extra tools on PATH
  metadata = { project = "x"; };
};
```

`packages.<system>.lab-py` is the `lab` module alone, with no dependencies, ready
to put in any Python environment.
You can also produce `experiment.json` yourself (see nix-deploy's README); the
wrapper is a convenience, not a requirement.

## Environment of a run

`NIX_LAB_DIR` (record directory), `NIX_LAB_RUN` (run id), `NIX_LAB_SEED`,
`NIX_LAB_PARAMS` (JSON). They are set for you by `nix-lab run`.

## Status

Verified (on camarade): typed record validation, spec loading with `[data]` and
`[pipeline]`, the runner over nix-deploy's local provider with two hosts (4 jobs,
all `ok`), compaction to Parquet, and the R pipeline with provenance, using the
pinned `r-env` and `labr`. Unit tests: `tests/test_lab.py`.

Not built yet: an R recorder (not wanted); migrating nerve's `data/` into runs (nerve's own
task, using this as its record format); declaring the Rust crate as a Nix package.
