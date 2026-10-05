# nixsci

`nixsci <command> [args]` is the only executable. It runs the entry point of the same name in the group `nixsci.commands` and passes it the rest of the line.

| command | package |
|---|---|
| `nixsci deploy` | `nixsci.deploy` |
| `nixsci lab` | `nixsci.lab` |

A package adds a command by declaring `[project.entry-points."nixsci.commands"]` with a function that takes `argv` and returns an exit code.
