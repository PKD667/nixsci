# nixsci

nixsci deploys Nix closures, records experiments, runs R or Python analyses over locked data, and builds documents from their outputs.

```sh
nix run github:PKD667/nixsci -- --help
```

| Command | Package | Reads |
|---|---|---|
| `nixsci deploy` | `nixsci.deploy` | [deploy/README.md](deploy/README.md), [deploy/DESIGN.md](deploy/DESIGN.md) |
| `nixsci lab` | `nixsci.lab` | [lab/README.md](lab/README.md), [lab/SPEC.md](lab/SPEC.md), [lab/DESIGN.md](lab/DESIGN.md) |
| statistics | `nixsci.stats` | [stats/README.md](stats/README.md) |
| `nixsci out` | `nixsci.out` | [out/README.md](out/README.md) |
| `nixsci <command>` | `nixsci.cli` | [cli/README.md](cli/README.md) |

Nix pins the toolchain. Peer stores share sealed runs by content hash. No machine or organisation
has a distinguished role.

The target is traceable, formally specified claims throughout a paper, from recorded observations
through analysis and statistical inference to each expression. Today the code establishes
provenance and executes claim checks. It does not prove R or Python analyses or the coverage of
its statistical procedures. Formal proofs will remain conditional on stated models and sampling
assumptions. A successful build is not scientific or formal verification.

Layout: each directory is one package of the `nixsci` namespace. Put the parent of your checkout on `PYTHONPATH`, and the checkout directory must be named `nixsci`, to import it from source.
