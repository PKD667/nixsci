# lab

Experiment records and run specs. An experiment, in any language, records what
it measured in one line. Runs are described in TOML, expanded into seeds and
parameter sweeps, executed on any host, and their records come back next to a
manifest saying exactly which code produced them.

The record side has no dependencies. Running experiments remotely uses
[`nixsci.deploy`](../deploy/README.md); `nixsci.lab` never reimplements deployment.

## Recording (inside an experiment)

```python
from nixsci import lab

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
from nixsci import lab
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
nixsci lab plan experiments/blocks.toml          # list the jobs this expands to
nixsci lab run  experiments/blocks.toml --out runs
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
columns (`run`, `seed` and `time` are reserved: nixsci.lab adds them). Once a spec
declares datasets, JSON values must be rows of a declared dataset; arrays, bytes
and files still record freely as artifacts. The schema travels to the run in
`NIX_LAB_SCHEMA` (keys in `NIX_LAB_KEYS`) and is copied into each run's `manifest.json`.

## Recording a run by hand

A script that is not launched by `nixsci lab run` (a measurement you start yourself, a long
service) records through `lab.Run`. It creates `<root>/<app>/<name>/`, applies the spec's
declared datasets and keys to `lab.record`, and writes `manifest.json` when the block ends
(`ok`, or `failed` if it raised), so `compact` and `analyze` treat it like any other run:

```python
from nixsci import lab

with lab.Run("runs", lab.run_name("meas", seed=3), spec="measure.toml", seed=3,
             params={"workers": 62}) as run:
    run.record("size", {"n": 1000, "seconds": 1.5})     # same as lab.record inside the block
```

`app` defaults to the spec's `[experiment] name` (or pass `app=`). The environment variables
`lab.record` reads are set for the block and restored afterwards. The manifest layout is in
`SPEC.md`; nothing else needs to be written by hand.

## Where the data lives

Outputs belong to the project and are not shared. A project is the nearest directory with a `.git`,
searched from the spec or the working directory upwards, or else the directory of the spec. Its
outputs live in `<project>/.nixsci/`, which ignores itself in git: `runs/` (immutable, sealed),
`data/` (Parquet, rebuildable) and `analysis/` (pipeline outputs, rebuildable). `$NIXSCI_STORE` or
`--store` puts them somewhere else. Nothing needs a directory argument: `lab.Run(None, name,
spec=...)`, `nixsci lab run`, `compact` and `analyze` all default to it, and `nixsci lab ls` shows
what is in it. Inputs are the exception: see [Inputs](#inputs-datasets-and-models).

```sh
nixsci lab build experiments/demo.toml            # experiment: run what is missing -> compact -> lock
nixsci lab build experiments/demo-analysis.toml   # analysis: run its pipelines over the locked data -> lock
```

`build` is incremental: finished inputs are skipped, compacted runs are not rewritten, unchanged
pipelines are not rerun. It writes `<spec>.lab.lock` beside the spec. **Commit that file.** An
experiment's lock lists its finished runs (manifest and records hashes, closure, source, machine).
An analysis' lock pins the experiment locks it uses by hash, lists their runs, and holds each
pipeline's output hashes -- a few KB of hashes, no data. These locks are the project's
reproducibility claim.

```sh
nixsci lab check experiments/demo.toml                 # does this machine's store match the lock?
nixsci lab push  me@host:/srv/lab experiments/demo.toml   # send the locked runs and outputs
nixsci lab pull  me@host:/srv/lab experiments/demo.toml   # fetch what is missing, verify by hash
```

A *remote* is any directory with the store layout, local or over ssh; there is no server. Runs
are immutable, so they are copied once; everything is checked against the lock afterwards. On a
fresh machine: clone the project, `nixsci lab pull <remote> <spec>`, then `nixsci lab check`.

## Replicates, identity and reproducing a run

A run is identified by its **inputs**: the experiment closure (so the code and every dependency),
the parameters, the seed and the declared schema. Runs with the same inputs are *replicates*.

```toml
[experiment]
replicates = 3          # at least 3 finished runs per input (default 1)

[data.size]
columns = { n = "int", seconds = "float" }
key = ["n"]
noisy = { seconds = 0.25 }   # seconds may differ by up to 25% between replicates; others must match
```

- `nixsci lab run` is **idempotent and resumable**: inputs that already have enough finished
  replicates are skipped, so a preempted sweep continues where it stopped, and changed code (a new
  closure) is a new input. `nixsci lab run --again` adds one more replicate.
- Every run is **sealed**: its manifest holds the SHA-256 of `records.jsonl`, of the spec (a copy is
  stored as `spec.toml`), the source as locked (git rev, narHash), the closure, and the machine
  (hostname, kernel, CPU, cores, memory). `compact` refuses a run whose records changed afterwards.
- `nixsci lab repro <run>` prints everything needed to run that measurement again (the flake as a
  locked reference, parameters, seed, expected hash, machine).
- `nixsci lab verify <run>` rebuilds the code from the locked source, runs one new replicate and
  compares the datasets. Exact columns must match; `noisy` columns must agree within their
  tolerance. It exits 0 only if they do.

nixsci.lab does not promise identical bytes: a measurement with an uncontrolled component (MPI timing,
a shared cluster) is a random variable. What it guarantees is exact *provenance* and honest
*re-measurement*. See `DESIGN.md` for the reasoning and the literature behind it.

## Inputs: datasets and models

Reference. Inputs are the data an experiment reads and the models it loads. They live in an input
store, which is meant to be shared: the default is `~/.nixsci`, `$NIXSCI_INPUTS` or `--inputs`
moves it, and a grid whose nodes share storage points it there, so the data is stored once for the
whole grid and read in place.

```sh
nixsci lab data import dataset shd shd.parquet --unit time=s --source https://example.org/shd
nixsci lab data import model nir-shd net.nir --io '{"input": {"shape": [700]}, "output": {"shape": [20]}}'
nixsci lab data ls
nixsci lab data show shd@2          # name, name@3 or name#<hash prefix>
nixsci lab data diff shd@1 shd@2    # columns, rows and bytes that changed
nixsci lab data verify shd          # re-hash the files
nixsci lab data path shd            # where the files are
```

| Kind | Holds | Learned from |
|---|---|---|
| `dataset` | One Parquet table. | The file: columns, types, row count. You add units and sources. |
| `model` | One file. | You add its inputs and outputs. |

What an import could not learn is listed as `gaps` and never guessed.

Each revision records the hash of its parent, and its number is its depth in that chain. No
allocator hands out numbers, so several machines can write to one store without locks. A bare name
means the tip. If two revisions extend the same parent, they carry the same number, and a reference
that cannot tell them apart fails and lists them; pin one with `name#<hash>`. Importing what the tip
already holds changes nothing. Layout, all written once and named by hash: `blobs/<sha256>`,
`manifests/<sha256>.json`, `names/<name>/<sha256>`.

## Compaction and analysis

```sh
nixsci lab compact runs --out data             # needs pyarrow (the nixsci.lab package has it)
nixsci lab analyze experiments/demo-analysis.toml --runs runs --data data --out analysis
```

`compact` writes each finished run's declared datasets as typed Parquet,
`data/<app>/<dataset>/<run id>.parquet`, with the declared columns plus `run`,
`seed` and `time`. Runs that did not end `ok` are skipped.

Analysis is R, and it is a spec of its own: an analysis never measures and an experiment never
aggregates. An analysis names under `[use]` the experiments it reads, and holds the pipelines:

```toml
# experiments/demo-analysis.toml
[analysis]
name = "demo-curves"

[use]
demo = "demo.toml"           # alias = the experiment spec whose locked runs this reads

[pipeline.curve]
script = "demo.R"            # path relative to the spec
deps = ["helpers.R"]         # other files the script uses (hashed with it)
```

An alias resolves through the experiment's `demo.lab.lock`: the analysis sees exactly the locked
runs, never a replicate or a half-finished sweep that arrived later. `analyze` stops, before R
starts, if the store does not hold what the lock names (`nixsci lab pull`, or `build` the
experiment). It then builds a *view*, a directory of symlinks to only those runs' Parquet files, and
runs the script with `NIX_LAB_VIEW` (the view) and `NIX_LAB_OUT` (`<out>/<pipeline>/`). No variable
names the store. The `nixsci` R package reads the view and has no function that reads anything else:

```r
library(dplyr); library(nixsci)
demo <- use("demo")             # an alias declared under [use]; any other name is an error
loss <- demo$loss |> collect()  # lazy Arrow dataset over the locked runs only
runs(demo)                      # one row per locked run; `params` is a list column
params(demo)                    # run plus one column per parameter
write.csv(summary, out("final.csv"))
```

A pipeline whose last run succeeded is skipped (`up to date`) while its script, its `deps` files
and its locked input runs (with their manifest hashes) are unchanged; `--force` reruns.

After each pipeline a `provenance.json` is written beside its outputs: the script and its hash,
every input run with the hash of its manifest, the experiment locks they came through, the R
version, the exit code. A figure or table can therefore say exactly which runs and which code
produced it. For pinned R packages run inside the flake's R environment (`nix build .#r-env`, then
`NIX_LAB_RSCRIPT=<out>/bin/Rscript`, or put it on `PATH`). `examples/demo.{toml,py,R}` and
`examples/demo-analysis.toml` are a complete worked example.

"Declared" holds by construction, not by sandbox: a script that guesses an absolute path into the
store can still read it.

## Recording from Rust

`rust/lab` is a small crate (serde_json + sha2) that writes the same format:

```rust
lab::record("loss", &serde_json::json!({"epoch": 3, "split": "train", "value": 0.25}))?;
lab::record_bytes("weights", &bytes, "application/octet-stream")?;
let epsilon = lab::params()["epsilon"].as_f64();   // lab::seed() -> Option<i64>
```

It enforces the same declared columns and keys as the Python module. The crate lives in
`rust/lab`: use it as a path dependency (`lab = { path = "../nixsci/lab/rust/lab" }`) or vendor it.
`tests/test_rust.py` builds
nothing itself: point `NIX_LAB_RUST_EMIT` at `cargo build --example emit` and Python checks that
it can read and validate what Rust wrote. A recorder in another language only has to follow
`SPEC.md`.

## Making a flake experiment

`lib.mkExperiment` wraps a program so it gets a record directory, forwards
SIGTERM, and always writes `status.json`:

```nix
experiments.${system}.blocks = nixsci.lib.mkExperiment pkgs {
  name = "blocks";
  program = "${python}/bin/python ${./blocks.py}";
  runtime = [ pkgs.clang ];           # extra tools on PATH
  metadata = { project = "x"; };
};
```

`packages.<system>.lab-py` is the `lab` module alone, with no dependencies, ready
to put in any Python environment.
You can also produce `experiment.json` yourself (see nixsci.deploy's README); the
wrapper is a convenience, not a requirement.

## Environment of a run

`NIX_LAB_DIR` (record directory), `NIX_LAB_RUN` (run id), `NIX_LAB_SEED`,
`NIX_LAB_PARAMS` (JSON). They are set for you by `nixsci lab run`.

## Status

Verified on camarade with the demo experiment: `nixsci lab build` (experiment: run -> compact -> lock; analysis: pipelines -> lock)
from an empty store, a second `build` that does nothing, `repro` and `verify` (rebuild from the
locked git revision, run a replicate, compare 5 rows against 5), `push` to a directory and `pull`
into an empty store ending in `store matches the lock`. 35 unit tests cover identity and
replicates, sealing and tamper detection, noisy-column comparison, the lock, and push/pull.

Not built yet: claim checks in `verify` (an analysis pipeline that fails when a claim fails is
already one; `verify --claims` would run them on the replicates), export of a run as a Workflow
Run RO-Crate, confidence-interval helpers in the `nixsci` R package for replicates, garbage collection of the
store, comparison of artifact (array) records, and recorders for R (not wanted). Migrating nerve's
`data/` into runs is nerve's own task. The reasoning behind the design, with the literature, is in
`DESIGN.md`.
