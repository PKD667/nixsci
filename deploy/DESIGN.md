# `nix_deploy`

The invariant is an executable immutable flake-derived closure with explicit
private inputs. Placement changes the target and transport, never the
software or its scientific metadata.

## Controller boundary

`resolve(flake, experiment, system)` is controller-only. It evaluates and
captures an immutable flake source first, then builds the derivation
`experiments.${system}.${experiment}` from that exact store snapshot. A local
flake is accepted only as a clean Git checkout with a commit; dirty or
untracked local state is rejected without copying it. The derivation must
contain `experiment.json` at its root. The manifest's `program` and any `commands` entries are absolute store
executables; a command may live in any dependency in the complete closure.
`trainingSpec`, `code`, and other scientific values remain opaque metadata.

The returned `Closure` is the only structured deployment value. It records the
root `path`, selected `program`, normalized `metadata`, every closure path and
its NAR hash in `closure`, locked flake `source` identity, and `system`.
Resolution verifies Nix's complete recursive closure before returning. A
backend verifies the same path-to-NAR map again before execution.

No compute target evaluates a flake or builds an experiment. A target only
stages an already resolved closure. Private datasets and checkpoints are
copied into a mode-700 run directory outside the Nix store and their hashes
are recorded in the durable handle; they are never added to the store.

## Target protocol

Native and SSH use one lifecycle:

```python
backend.stage(closure)
handle = backend.launch(closure, run_id="...", argv=(), env=None,
                        inputs=None, program="run")
backend.read_events(handle, offset)
backend.alive(handle)
backend.stop(handle)
backend.fetch(handle, path, destination)
backend.read_file(handle, path)
backend.exists(handle, path)
backend.remove(handle)
backend.tunnel(handle, port)
```

The manifest normalizer handles ordinary runnable packages and explicit
`packages.<system>.*` installables as well as experiments without importing
the training layer; it performs one selected installable lookup, never attr fallback.
`program`, `commands`, `argv`,
`env`, and `resources` are deployment values; deployment does not interpret
training fields.

A native target declares either the existing `/nix/store` or an explicit
rootless store. These modes are not selected by fallback. Rootless targets
require a locally hash-verified static Nix bootstrap. SSH stages that exact
bootstrap at the declared target path, copies the full closure through a
controller-created Nix binary cache, verifies every remote NAR hash, then
starts the payload through upstream `nix shell --offline`. The SSH client is
backgrounded after authentication while the remote script remains foreground;
there is no detached `cd && background` command whose inherited channel can
block the launch.

The public interface is `python -m nix_deploy` (the `run`, `status`, `stop`,
and `fetch` commands). Handles are JSON, mode 600, and contain enough
closure/source/input identity to replay a lifecycle operation. There is no
resident deployment service.
