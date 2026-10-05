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

Working: the record format and Python module; spec loading with seeds, sweeps and
strict validation; the runner over nix-deploy providers; `mkExperiment`.
Checked by import and plan expansion, and by recording/reading round trips. The
runner itself has not been run end to end on the current nix-deploy.

Not built yet (planned, in this order): declared dataset schemas validated at
record time; compaction of collected runs into typed Parquet (Arrow) partitions;
R analysis pipelines (an R package reading runs as data frames, pipelines as
pinned Nix derivations that write their outputs and provenance back);
recording libraries for Rust and R; replacing project-local data directories
(for example nerve's `data/`) with lab runs.
