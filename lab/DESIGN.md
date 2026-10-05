# lab design notes

Why nixsci.lab is shaped the way it is, what the literature and existing tools say, and what was
deliberately not done. Written before the storage and verification layers were built, so a
later rework can start from the reasons instead of rediscovering them.

## Goals, in order

1. **Reproducibility that is honest about noise.** Nerve-class experiments (MPI, shared
   clusters) are not deterministic and never will be. The promise is exact *provenance* plus
   statistically sound *re-measurement*, not identical bytes.
2. **Nothing to manage by hand.** No data directory to name, no metadata to write, no server.
3. **Decentralised.** Every machine has its own store; moving data between machines needs only
   ssh. No central database, no hosted service.
4. **Minimal.** One record format, one identity rule, a lock file. Reuse standards where they
   fit (Parquet, Arrow, RO-Crate); do not invent a workflow language.

## Terms (ACM artifact review and badging)

*Repeatability*: same team, same setup. *Reproducibility*: different team, same setup (the
authors' own artifacts). *Replicability*: different team, different setup. nixsci.lab can promise
the first two for a published result: the code and environment are pinned (Nix), the inputs are
recorded, and `verify` re-measures. Replicability is the scientific claim itself and is outside
any tool. [ACM policy](https://www.acm.org/publications/policies/artifact-review-and-badging-current);
HPC-focused survey: [arXiv:2402.07530](https://arxiv.org/pdf/2402.07530).

## What existing systems do, and what we took

| system | idea | taken / not taken |
|---|---|---|
| **DVC** ([internal files](https://doc.dvc.org/user-guide/project-structure/internal-files)) | content-addressed cache; small pointer / `dvc.lock` files committed to git; `push`/`pull` to ssh/S3 remotes | **Taken**: the lock file as the committed claim, store + remotes. Not taken: per-project `.dvc/cache` inside the repo as the default. |
| **DataLad / git-annex** ([provenance capture](https://docs.datalad.org/en/stable/design/provenance_capture.html), [paper](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11514317/)) | `datalad run` records command + inputs + outputs; `rerun` re-executes and checks file hashes; fully decentralised | **Taken**: actionable provenance, decentralisation, rerun-and-check. Not taken: byte-hash comparison (assumes determinism) and a shared mutable repository: on HPC, concurrent jobs on one dataset repo needed a dedicated extension ([arXiv:2505.06558](https://arxiv.org/abs/2505.06558)). Our runs are write-once directories, so there is nothing to lock. |
| **Sumatra** ([repo](https://github.com/open-research/sumatra)) | records code version, parameters, platform; `smt repeat` checks out the version, reruns, compares outputs | **Taken**: this is `repro`/`verify`. Its limits (local filesystem only, comparison of outputs) are the gaps we fill. |
| **Sacred / MLflow** ([observers](https://sacred.readthedocs.io/en/stable/observers.html), [tracking](https://mlflow.org/docs/latest/tracking/)) | run records in MongoDB / SQL / a tracking server, plus an artifact store | **Not taken**: a database or server is the thing to avoid. Both started file-based; we stay there. |
| **targets** ([data and metadata](https://books.ropensci.org/targets-design/data.html), [gittargets](https://github.com/ropensci/gittargets)) | hash-based "up to date" detection, a store with centralised metadata, git snapshots of the store | **Taken**: the `fingerprint` skip in `analyze` follows the same idea. |
| **Nix / Guix** ([Guix for research](https://www.nature.com/articles/s41597-022-01720-9)) | pure builds; bit-for-bit as a foundation to isolate what changed, "not a goal in itself" | **Taken**: input-addressing. We hash *inputs*; outputs of a noisy experiment cannot be content-addressed. |
| **Workflow Run RO-Crate** ([arXiv:2312.07852](https://arxiv.org/abs/2312.07852)) | machine-actionable, PROV-aligned record of a run, including re-execution | **Planned**: export a run as a crate. The manifest stays minimal and maps onto it; we do not replace it. |
| **Popper / Aver** ([convention](http://alumni.soe.ucsc.edu/~msevilla/papers/jimenez-ipdpsw17.pdf), [validation](https://dl.acm.org/doi/10.1145/3184407.3184422)) | experiment as a DevOps project; declarative assertions that a *claim* holds on every re-execution | **Taken** as the answer for noisy results: verify claims, not rows (see below). |
| **Hoefler & Belli** ([SC15](https://dl.acm.org/doi/10.1145/2807591.2807644)) | report whether data is deterministic; confidence intervals for non-deterministic data; document the setup | **Taken**: `noisy` columns, `replicates`, the machine record. |
| **Hunold & Carpen-Amarie** ([MPI benchmarking](https://arxiv.org/pdf/1505.07734)) | MPI timings need careful experimental design and enough measurements | Informs `replicates`; the measurement design itself belongs to the experiment, not to nixsci.lab. |

## Decisions

1. **A run is identified by its inputs.** `input_id` = hash of (app, closure store path, params,
   seed, declared schema and keys). The closure path already pins code and every dependency.
   Same `input_id` = same measurement; a changed line of code is a new input.
2. **Replicates, not duplicates.** Runs sharing an `input_id` are samples of one measurement.
   `[experiment] replicates = N` asks for at least N finished runs per input. `run` is therefore
   idempotent and resumable (a preempted sweep continues where it stopped).
3. **Runs are immutable.** A run directory is written once and sealed with the SHA-256 of its
   `records.jsonl`; `compact` refuses data that no longer matches. No shared mutable state.
4. **Noise is declared, not hidden.** `[data.x] noisy = { seconds = 0.25 }` gives a relative
   tolerance; every other column must match exactly on re-measurement. Report the machine
   (hostname, kernel, CPU, cores, memory) with every run, as Hoefler & Belli ask.
5. **Verification compares claims where rows are noisy.** `verify` re-runs one replicate and
   compares within tolerance. The stronger statement for noisy systems, in the spirit of Aver,
   is a *claim* ("scaling efficiency stays above 0.7"): an analysis pipeline that exits non-zero
   when the claim fails is already a claim check. Planned: `verify --claims` runs the declared
   pipelines on original + replicates.
6. **Where data lives** (decided with the user's constraints: no central storage, no `<repo>/data`):
   - a **per-machine content store**, default `$XDG_DATA_HOME/nix-lab` (override with
     `NIX_LAB_STORE`), shared by all projects; runs are write-once and addressed by `input_id`;
   - a small **`<spec>.lab.lock`** next to each spec, committed to git. An experiment's lock lists
     its finished runs and their `records_sha256`; an analysis' lock pins the experiment locks it
     `use`s by hash and lists each pipeline's `fingerprint` and output hashes. The lock is the
     reproducibility claim; the bytes are not in the repository;
   - **remotes** are plain ssh directories (`nixsci lab push|pull <host:dir>`), moved with rsync.
     Any machine can fetch what the lock names and verify it by hash.
7. **No workflow language.** Experiments are Nix flake outputs, analysis is a script, the spec
   is TOML. Ordinary LaTeX consumes the outputs.

## Open questions

- Garbage collection of the store (by lock roots, like Nix): not built.
- Whether the lock should also pin pipeline *outputs* by hash (figures) so a paper build can
  fetch them: likely yes; blocked on nix-science's builder shape.
- Artifact (array) records are not compared by `verify`.
- Cross-machine replicate analysis (confidence intervals over replicates): a helper in the `nixsci` R package.
