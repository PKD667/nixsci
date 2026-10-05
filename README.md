# nixsci

Signpost. nixsci runs experiments from a Nix flake on any host, keeps their typed records and data, and runs R analyses over exactly the data they declare.

```sh
nix run github:PKD667/nixsci -- --help
```

| Command | Package | Reads |
|---|---|---|
| `nixsci deploy` | `nixsci.deploy` | [deploy/README.md](deploy/README.md), [deploy/DESIGN.md](deploy/DESIGN.md) |
| `nixsci lab` | `nixsci.lab` | [lab/README.md](lab/README.md), [lab/SPEC.md](lab/SPEC.md), [lab/DESIGN.md](lab/DESIGN.md) |
| `nixsci <command>` | `nixsci.cli` | [cli/README.md](cli/README.md) |

nixsci depends on Nix and Python only. Nothing in it knows any one organisation's machines; packages that do, such as [usinix](https://github.com/Unsuspicious-Industries/usinix), depend on it and add commands to it.

Layout: each directory is one package of the `nixsci` namespace. Put the parent of your checkout on `PYTHONPATH`, and the checkout directory must be named `nixsci`, to import it from source.
