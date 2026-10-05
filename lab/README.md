# lab

Experiments and analyses as Nix values. An experiment, in any language, records what it measured in one line. Nix describes the experiment (seeds, sweeps, declared data) and builds everything pure: the compaction of records into Parquet and the R analyses are derivations over the finished runs, built in the sandbox. Python only executes: it runs the jobs on hosts and adds each finished run to the Nix store.

The record side has no dependencies. Running on hosts uses [`nixsci.deploy`](../deploy/README.md); `nixsci.lab` never reimplements deployment.

## The pipeline

```
nix run .#demo            impure, Python: run the jobs, seal each run, add it to the Nix store,
                          list it in lab.lock.json                      (commit that file)
nix build .#demo-curves   pure, Nix: compact each run to Parquet, build a view of the runs the
                          lock names, run the R pipelines in the sandbox
```

Everything pure is cached by Nix. A new run compacts once, and only the analyses that use it rebuild. The derivation graph of a result is its provenance: `nix-store -qR $(nix path-info --derivation .#demo-curves)` lists the runs it was built from.

## Describing an experiment

In the project's flake:

```nix
let lab = nixsci.lib.lab { inherit pkgs; project = self; }; in {
  lab.${system}.experiments.demo = lab.experiment {
    name = "demo";
    closures.${system} = self.experiments.${system}.demo;   # what to run, built by Nix
    seeds = [ 0 1 ];                                        # one job per seed
    sweep.epsilon = [ 0.1 0.3 ];                            # every combination is a job
    params.size = 32;                                       # fixed, visible to lab.params()
    resources = { provider = "g5k"; hosts = 2; walltime = 120; opts.queue = "besteffort"; };
    data.loss.columns = { epoch = "int"; value = "float"; split = "str?"; };   # ? = may be null
  };
  apps.${system}.demo = self.lab.${system}.experiments.demo.run;
}
```

`closures` maps each system you deploy to a built experiment; `systems` (default: this machine's) names which of them to build. `resources.provider` is a name from `~/.config/nix-deploy/providers.toml`, and provider-specific settings go only under `resources.opts`. Unknown fields and a malformed name are evaluation errors.

`nix run .#demo` resolves nothing at run time: Nix already built the closures. The executor leases hosts, spreads the jobs over them, waits, fetches each run's `lab/` directory, writes its `manifest.json` (state `ok`, `failed` or `incomplete`, seed, parameters, target, times, the flake's source identity and the closure path), adds the run directory to the Nix store and lists it in `lab.lock.json`. Flakes only see git-tracked files, so `git add lab.lock.json`.

## Recording (inside an experiment)

```python
from nixsci import lab

lab.record("loss", 0.25, epoch=3)         # JSON-like value, stored inline
lab.record("weights", numpy_array)        # arrays, bytes and paths become content-addressed artifacts
lab.params()                              # this run's parameters (dict)
lab.seed()                                # this run's seed (int or None)
```

Values are JSON only. There is no pickle: anything else is stored as an artifact with a media type, so any language can read it back. Records are appended to `$NIX_LAB_DIR/records.jsonl`; large payloads go to `$NIX_LAB_DIR/artifacts/<sha256>`. The exact format is [SPEC.md](SPEC.md). It is the contract: a writer in another language only has to produce that format.

```python
rows = lab.load("path/to/a/run")                                # records of one run
lab.artifact(run_dir, rows[3])                                  # bytes of an artifact record
lab.runs(".nixsci/runs", app="demo", seed=1, params__epsilon=0.3)   # find run directories by manifest fields
```

## Typed datasets

`data.<name>.columns` declares a dataset, and `lab.record` checks every row against it, at record time, in the run itself:

```python
lab.record("loss", {"epoch": 3, "value": 0.25, "split": None})   # ok
lab.record("loss", {"epoch": 3.5, "value": 0.25, "split": "a"})  # TypeError, nothing written
lab.record("lossy", {...})                                       # ValueError: not declared
```

Add `key = [ "epoch" "split" ];` to a dataset and each key may appear once per run: a second row with the same key is refused at record time, and compaction re-checks the whole run. Key columns must be declared and not nullable. Types are `int`, `float`, `str`, `bool`. A row carries exactly the declared columns (`run`, `seed` and `time` are reserved: nixsci.lab adds them). The schema travels to the run in `NIX_LAB_SCHEMA` (keys in `NIX_LAB_KEYS`) and is copied into the run's `manifest.json`.

## Recording a run by hand

A script that the executor does not launch (a measurement you start yourself, a long service) records through `lab.Run`. Point it at the spec file Nix wrote: `nix build .#lab.<system>.experiments.demo.specFile`. It creates `<root>/<app>/<name>/`, applies the declared datasets and keys, and writes `manifest.json` when the block ends (`ok`, or `failed` if it raised):

```python
with lab.Run(None, lab.run_name("meas", seed=3), spec="result/demo-spec.json", seed=3,
             params={"workers": 62}) as run:
    run.record("size", {"n": 1000, "seconds": 1.5})
```

`root=None` means `<project>/.nixsci/runs`. Then `nixsci lab add <run directory>` adds the run to the Nix store and `lab.lock.json`, which makes it visible to analyses like any other run.

## Where files live

Runs are staged in `<project>/.nixsci/runs/<app>/<run>/`. A project is the nearest directory with a `.git`; the directory ignores itself in git, and `$NIXSCI_STORE` moves it. The runs that count are in the Nix store, named by `lab.lock.json`. Move them between machines with Nix:

```sh
nixsci lab push ssh-ng://host      # nix copy the locked runs to a store
nixsci lab pull ssh-ng://host      # fetch the locked runs from a store
```

Evaluating an analysis on a machine that lacks a locked run fails with that path's name: pull it first. Nix checks every path against the hash in the lock.

## Replicates, identity and reproducing a run

A run is identified by its **inputs**: the experiment closure (the code and every dependency), the parameters, the seed and the declared schema. Runs with the same inputs are *replicates*. `replicates = 3;` asks for at least three finished runs per input, and a dataset's `noisy.seconds = 0.25;` lets that column differ by up to 25% between replicates; other columns must match.

- `nix run .#demo` is **idempotent and resumable**: inputs that already have enough finished replicates are skipped, so a preempted sweep continues where it stopped, and changed code (a new closure) is a new input. `nix run .#demo -- --again` adds one more replicate.
- Every run is **sealed**: its manifest holds the SHA-256 of `records.jsonl` and of the spec (a copy is stored as `spec.json`), the source identity Nix reports, the closure, and the machine (hostname, kernel, CPU, cores, memory). Compaction refuses a run whose records changed afterwards, and so does the Nix hash in the lock.
- `nixsci lab repro <run>` prints everything needed to run that measurement again.
- `nixsci lab verify <run>` runs one new replicate from the closure the run used and compares the datasets. Exact columns must match; `noisy` columns must agree within their tolerance. It exits 0 only if they do.

nixsci.lab does not promise identical bytes: a measurement with an uncontrolled component (MPI timing, a shared cluster) is a random variable. What it guarantees is exact *provenance* and honest *re-measurement*. [DESIGN.md](DESIGN.md) gives the reasoning and the literature.

## Analyses

An analysis is a derivation. It never measures and an experiment never aggregates:

```nix
lab.${system}.analyses.demo-curves = lab.analysis {
  name = "demo-curves";
  use.demo = experiments.demo;                 # alias = an experiment; its locked runs are the input
  pipelines.curve.script = ./demo.R;           # a path: the script is hashed with the build
};
packages.${system}.demo-curves = self.lab.${system}.analyses.demo-curves;
```

`nix build .#demo-curves` compacts each locked run to typed Parquet (`<dataset>/<run id>.parquet` with the declared columns plus `run`, `seed` and `time`), links exactly those files into a view, and runs each script with `Rscript` in the sandbox with `NIX_LAB_VIEW` (the view) and `NIX_LAB_OUT` (the pipeline's output directory, which becomes the result). The sandbox has no network and no other runs: a script cannot read what it did not `use`, and it cannot fetch anything. Constitute outside data into a dataset first, then use it by hash.

The `nixsci` R package reads the view and has no function that reads anything else:

```r
library(dplyr); library(nixsci)
demo <- use("demo")             # an alias declared under `use`; any other name is an error
loss <- demo$loss |> collect()  # lazy Arrow dataset over the locked runs only
runs(demo)                      # one row per locked run; `params` is a list column
params(demo)                    # run plus one column per parameter
write.csv(summary, out("final.csv"))
```

## Inputs: datasets and models

Reference. Inputs are the data an experiment reads and the models it loads. They live in an input store, which is meant to be shared: the default is `~/.nixsci`, `$NIXSCI_INPUTS` or `--inputs` moves it, and a grid whose nodes share storage points it there, so the data is stored once for the whole grid and read in place.

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

What an import could not learn is listed as `gaps` and never guessed. Each revision records the hash of its parent, and its number is its depth in that chain. A bare name means the tip. If two revisions extend the same parent, a reference that cannot tell them apart fails and lists them; pin one with `name#<hash>`. This store is being replaced by datasets and models as Nix store objects, so that analyses can `use` them like runs.

## Recording from Rust

`rust/lab` is a small crate (serde_json + sha2) that writes the same format:

```rust
lab::record("loss", &serde_json::json!({"epoch": 3, "split": "train", "value": 0.25}))?;
lab::record_bytes("weights", &bytes, "application/octet-stream")?;
let epsilon = lab::params()["epsilon"].as_f64();   // lab::seed() -> Option<i64>
```

It enforces the same declared columns and keys as the Python module. Use it as a path dependency (`lab = { path = "../nixsci/lab/rust/lab" }`) or vendor it. `tests/test_rust.py` builds nothing itself: point `NIX_LAB_RUST_EMIT` at `cargo build --example emit` and Python checks that it can read what Rust wrote. A recorder in another language only has to follow `SPEC.md`.

## Making a flake experiment

`lib.mkExperiment` wraps a program so it gets a record directory, forwards SIGTERM, and always writes `status.json`:

```nix
experiments.${system}.blocks = nixsci.lib.mkExperiment pkgs {
  name = "blocks";
  program = "${python}/bin/python ${./blocks.py}";
  runtime = [ pkgs.clang ];           # extra tools on PATH
  metadata = { project = "x"; };
};
```

`packages.<system>.lab-py` is the `lab` module alone, with no dependencies, ready to put in any Python environment.

## Environment of a run

`NIX_LAB_DIR` (record directory), `NIX_LAB_RUN` (run id), `NIX_LAB_SEED`, `NIX_LAB_PARAMS` (JSON). The executor sets them.

## Status

Verified on camarade with the demo: `nix run .#demo` runs four jobs and adds four runs to the Nix store and the lock; `nix build .#demo-curves` compacts them and runs R in the sandbox; a second build builds nothing; a pipeline that lists `/nix/store` sees no raw run and no network.

Not built yet: datasets and models as Nix store objects that analyses `use`, runs that `use` other runs' artifacts, claim checks in `verify`, export of a run as a Workflow Run RO-Crate, confidence-interval helpers in the `nixsci` R package, comparison of artifact (array) records, and grid-wide materialization of inputs.
