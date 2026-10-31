# nixsci

nixsci deploys Nix closures, records experiments, runs R or Python analyses over locked data, and builds documents from their outputs.

Deterministic pass/fail checks (a compile, a bounded model check, a seeded fuzzer) are verdicts: `nixsci validate` builds them, and the Nix store holds each one. See [validate/README.md](validate/README.md).

```sh
nix run github:PKD667/nixsci -- --help
```

| Command | Package | Reads |
|---|---|---|
| `nixsci deploy` | `nixsci.deploy` | [deploy/README.md](deploy/README.md), [deploy/DESIGN.md](deploy/DESIGN.md) |
| `nixsci lab` | `nixsci.lab` | [lab/README.md](lab/README.md), [lab/SPEC.md](lab/SPEC.md), [lab/DESIGN.md](lab/DESIGN.md) |
| `nixsci validate` | `nixsci.validate` | [validate/README.md](validate/README.md) |
| statistics | `nixsci.stats` | [stats/README.md](stats/README.md) |
| `nixsci doc` | `nixsci.doc` | [doc/README.md](doc/README.md) |
| `nixsci <command>` | `nixsci.cli` | [cli/README.md](cli/README.md) |
| GPU probe (`experiments.<system>.gpu-probe`) | `gpu/` | [gpu/README.md](gpu/README.md) |

Nix pins the toolchain. Peer stores share sealed runs by content hash. No machine or organisation
has a distinguished role.

The target is traceable, formally specified claims throughout a paper, from recorded observations
through analysis and statistical inference to each expression. Today the code establishes
provenance and executes claim checks. It does not prove R or Python analyses or the coverage of
its statistical procedures. Formal proofs will remain conditional on stated models and sampling
assumptions. A successful build is not scientific or formal verification.

Layout: each directory is one package of the `nixsci` namespace. Put the parent of your checkout on `PYTHONPATH`, and the checkout directory must be named `nixsci`, to import it from source.

## Run on your machines

You need nix here, and `ssh HOST` working without a password to each machine (`~/.ssh/config`; check with `ssh -o BatchMode=yes HOST true`).

1. Describe the experiment in your flake: see [lab/README.md](lab/README.md#describing-an-experiment).
2. Run it on your machines, named as in ssh (no provider file):

   ```sh
   nix run .#demo -- --hosts alpha,beta      # or: NIXSCI_HOSTS=alpha,beta nix run .#demo
   ```
3. The first machine must reach the others by the same names, without a password.
4. A machine that asks for a password stops the run before anything is copied, with one line naming it.
5. Commit `lab.lock`, so the runs stay with the code that made them.
