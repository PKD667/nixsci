# `nixsci.deploy`

The invariant is an executable immutable flake-derived closure with explicit
private inputs. Placement changes the target and transport, never the
software or its scientific metadata.

## Controller boundary

`resolve(flake, deployment, system)` is controller-only. It captures an
immutable flake source, then builds `deployments.${system}.${deployment}` from
that exact snapshot. If that output is absent, it warns and falls back to the
legacy `experiments` output. A local flake is accepted only as a clean Git
checkout with a commit; dirty or untracked local state is rejected. New
closures contain `deployment.json`; old unversioned `experiment.json` remains
readable. The manifest's `program` and `commands` entries are absolute store
executables; a command may live in any dependency in the complete closure.
`trainingSpec`, `code`, and other scientific values remain opaque metadata.

The returned `Closure` is the only structured deployment value. It records the
root `path`, selected `program`, normalized `metadata`, every closure path and
its NAR hash in `closure`, locked flake `source` identity, and `system`.
Resolution verifies Nix's complete recursive closure before returning. A
backend verifies the same path-to-NAR map again before execution.

No compute target evaluates a flake or builds a deployment. A target only
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

Every target uses an explicit user-owned private store and a private
mount-namespace root at canonical `/nix/store`. Only the controller uses its
global store to resolve a closure; compute targets never read or mount it. The
Nix-built `nixsci` package carries the static Nix 2.34.8 helper pinned by this
flake. The helper requires unprivileged user and mount namespaces, maps the caller's UID/GID to the same host IDs, makes mounts private,
binds the target store at `/nix/store`, and uses `pivot_root`. The target
process retains its unprivileged host identity with zero effective setup
capabilities. Nested namespaces remain the candidate sandbox; the store view is
not a security boundary. The helper never falls back to host-store execution or a
mount-only namespace.

SSH first verifies the bootstrap hash, then imports only absent closure paths
into the declared store. Nix verifies the recursive NAR contents and the target
path-to-NAR map after staging and again before launch. A framed export carries
path, reference and NAR metadata without a temporary binary cache. Transport
never changes uncompressed NAR identity. The SSH client is backgrounded after
authentication while the remote script remains foreground; there is no detached
`cd && background` command whose inherited channel can block the launch.

The public interface is `python -m nixsci.deploy` (the `run`, `status`, `stop`,
and `fetch` commands). Handles are JSON, mode 600, and contain enough
closure/source/input identity to replay a lifecycle operation. There is no
resident deployment service.
