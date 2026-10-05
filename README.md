# nix-deploy

Run an immutable Nix experiment on any machine, with one contract everywhere.

You give it a flake reference and an experiment name. It builds
`experiments.<system>.<name>`, verifies the whole closure by NAR hash, copies it
to a target, runs it there, and gives you a handle to watch it, read its files,
tunnel to its ports and stop it. Where the machines come from (your laptop, an
ssh box, a Grid'5000 reservation, a site-specific broker) is a separate,
pluggable question: a *provider*.

It is meant to be the only way experiments are deployed. If a project has its
own artifact builder, ssh launcher or reservation script, the goal is to delete
it, not to keep it beside this.

Python 3.11+, standard library only. Needs `nix` on the machine that runs the
controller and, for non-Nix hosts, an ssh client.

## Concepts

| term | meaning |
|---|---|
| **experiment** | a store directory built from the flake, containing `experiment.json` |
| **closure** | the experiment plus everything it needs, identified by path to NAR hash |
| **backend** | something that can stage and run a closure: `native` (this machine), `ssh` (a remote host) |
| **provider** | gives you hosts and takes them back: `local`, `static`, `oar`, or a plugin |
| **lease** | the hosts a provider gave you, plus what is needed to release them |
| **handle** | JSON describing one running job (id, workdir, pid, closure, inputs) |

The resolving machine (the *controller*) does the `nix build`. Targets never
evaluate or build anything: they only receive the verified closure. Heavy builds
therefore belong on a machine meant for it, not on a laptop.

## The experiment contract

A flake exposes `experiments.<system>.<name>`, a derivation whose root holds
`experiment.json`:

```json
{
  "program": "/nix/store/...-thing/bin/run",
  "commands": { "inference": "/nix/store/...-thing/bin/infer" },
  "argv": [],
  "env": { "KEY": "value" },
  "resources": { "driver": null, "profile": null },
  "inputs": {},
  "metadata": { "anything": "opaque to nix-deploy" }
}
```

`program` and every `commands` entry must be a file inside the closure.
Unknown fields are kept as metadata. Nothing else about the experiment is
interpreted.

A flake is only accepted when it is a clean, committed git checkout (or a
remote ref). A dirty tree is refused rather than copied.

## What a job sees

Only the environment you declare, plus `PATH` set to the host's standard tool
directories (the closure should bring everything else). nix-deploy adds:

| variable | meaning |
|---|---|
| `NIX_DEPLOY_WORKDIR` | the job's private working directory |
| `NIX_DEPLOY_INPUT_<name>` | path of each private input you passed |
| `NIX_DEPLOY_HOSTFILE` | multi-host only: file listing hosts, one per line, first is this host |
| `NIX_DEPLOY_RSH` | multi-host only: command that opens a shell on another host (default `ssh`) |
| `NIX_DEPLOY_ENTER` | prefix that runs a command inside the same store view on whichever host it runs on |

stdout and stderr go to `process.log` in the workdir. The program stays
foreground in its own process group; `stop` sends SIGTERM to that group.

## Machine configuration

What a provider *name* means is configured per machine, in
`~/.config/nix-deploy/providers.toml`. Callers say `provider = "g5k"` and never
care how:

```toml
[providers.g5k]
use = "oar"                    # direct: your own ssh keys to the OAR frontend
login = "me"
site = "lille"
bootstrap = "/path/to/nix-static"          # a static nix binary, shipped to nodes
bootstrap_sha256 = "<sha256 of that file>"
```

On a server with a shared-credential broker, the same name can instead say
`use = "<installed plugin>"`. Code above the provider layer does not change.

Generic resources are the same for every provider: `hosts`, `gpus`, `walltime`
(minutes), `system`. **Everything provider-specific goes in `opts`**, and each
provider rejects options it does not know. Putting `site` among the generic
resources is an error.

## Command line

```sh
nix-deploy lease acquire g5k warm --hosts 4 --walltime 240 [--opt queue=besteffort]
nix-deploy lease ls
nix-deploy lease show warm
nix-deploy --lease warm run . my-experiment run1 --handle run1.json --arg 8 --env SEED=3
nix-deploy --lease warm status run1.json
nix-deploy --lease warm fetch run1.json out/ ./results
nix-deploy --lease warm stop run1.json
nix-deploy lease release warm
```

A lease outlives the process that acquired it (state is under
`~/.local/state/nix-deploy/leases/`), so a warm allocation can be used by many
later commands. `--config FILE --target NAME` addresses one static target from
a TOML file instead of a lease.

A target in that file looks like:

```toml
[targets.box]
backend = "ssh"
host = "user@host"
system = "x86_64-linux"
store = "/tmp/user/store"        # rootless store on the host
run_root = "/tmp/user/runs"
rootless = true
bootstrap = "/local/path/to/nix-static"
bootstrap_sha256 = "..."
remote_bootstrap = "/tmp/user/bin/nix"
ssh_options = ["-o", "ProxyJump=user@jump"]
```

`backend = "native"` runs on the controller's own store (`rootless = false`,
`store = "/nix/store"`).

## Python API

```python
from nix_deploy import providers, factory, resolver, group, leases

lease = providers.acquire("g5k", {"hosts": 2, "walltime": 240})      # or leases.load("warm")
configs = {f"t{i}": c for i, c in enumerate(lease.targets)}
backends = [factory.backend(name, configs) for name in configs]

closure = resolver.resolve(FLAKE, "my-experiment", lease.targets[0]["system"])
handle = group.launch(backends, lease.hosts, closure, run_id="s1", argv=("a", "b"))
#   one host? backends[0].stage(closure); backends[0].launch(closure, run_id=..., ...)

b = backends[0]
b.alive(handle)                         # bool
b.read_file(handle, "process.log")      # text or None
b.read_events(handle, offset)           # bytes of events.jsonl from offset
b.exists(handle, "relative/path"); b.fetch(handle, "relative/path", dest)
b.put(handle, "name", path_or_bytes)    # hash-verified file into the running job's workdir
port, close = b.tunnel(handle, REMOTE_PORT, host=NODE)   # local TCP forward; host defaults to the backend host
b.stop(handle); b.remove(handle)
lease.release()
```

`group.launch` stages the closure on every host and starts the program once, on
the first, with the hostfile in its environment. A program that starts MPI (or
any multi-host service) uses `NIX_DEPLOY_RSH` for the transport and
`NIX_DEPLOY_ENTER` as the launch-agent prefix so remote ranks run inside the same
store view.

## Writing a provider or backend

Both are plain classes found through Python entry points, so site-specific code
lives in its own package and nix-deploy never names it:

```toml
[project.entry-points."nix_deploy.providers"]
my-site = "my_site.provider:Provider"
[project.entry-points."nix_deploy.backends"]
my-backend = "my_site.backend:Backend"
```

A provider has `acquire(resources, opts) -> Lease` and `release(state)`; the
`Lease` carries `targets` (backend configs, one per host), `hosts` (names
reachable from inside the allocation) and a JSON-able `state` that `release`
needs, so a lease can be released from another process. A backend takes its
config dict and implements the methods above. See `nix_deploy/providers.py`
(`Local`, `Static`, `OAR`) and `nix_deploy/ssh.py`.

## Testing

```sh
PYTHONPATH=. python3 tests/e2e.py local 1       # resolve, stage, run, read the log
PYTHONPATH=. python3 tests/e2e.py g5k 2         # same on a real reservation
```

The flake's `experiments.<system>.hello` prints where it ran, which hosts it
saw, and its `NIX_DEPLOY_ENTER`. The tree must be committed first.

## Status

Verified: `local` provider and `native` backend end to end; lease
acquire/use/release through the CLI; provider option validation; `put` on the
native backend.

Not yet verified on real machines: the `oar` provider against Grid'5000, the
`ssh` backend with a rootless store, multi-host `group.launch`, `tunnel` to a
second host, `put` over ssh. Treat failures there as bugs to report.

Limits to know: the ssh backend needs unprivileged user namespaces on the target
(`nix --store` with a local root); `x86_64-linux` and `aarch64-linux` only; the
`nv`-style GPU toolchains are the experiment's business, not nix-deploy's.
