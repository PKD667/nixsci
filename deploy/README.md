# deploy

Run an immutable Nix experiment on any machine, with one contract everywhere.

You give it a flake reference and an experiment name. It builds
`experiments.<system>.<name>`, verifies the whole closure by NAR hash, copies it
to a target, runs it there, and gives you a handle to watch it, read its files,
tunnel to its ports and stop it. Where the machines come from (your laptop, ssh
hosts you already have, a site-specific broker) is a separate, pluggable question:
a *provider*. nixsci.deploy contains **no scheduler code**: reserving machines
(OAR, Slurm, a cloud API...) is somebody else's job, done by hand, by a project's
own script, or by a provider plugin. nixsci.deploy starts at "here are hosts I can ssh to".

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
| **provider** | gives you hosts and takes them back: `local`, `static`, or a plugin |
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
  "metadata": { "anything": "opaque to nixsci.deploy" }
}
```

`program` and every `commands` entry must be a file inside the closure.
Unknown fields are kept as metadata. Nothing else about the experiment is
interpreted.

A flake is only accepted when it is a clean, committed git checkout (or a
remote ref). A dirty tree is refused rather than copied.

## What a job sees

Only the environment you declare, plus `PATH` set to the host's standard tool
directories (the closure should bring everything else). nixsci.deploy adds:

| variable | meaning |
|---|---|
| `NIX_DEPLOY_WORKDIR` | the job's private working directory |
| `NIX_DEPLOY_INPUT_<name>` | path of each private input you passed |
| `NIX_DEPLOY_HOSTFILE` | multi-host only: file listing hosts, one per line, first is this host |
| `NIX_DEPLOY_RSH` | multi-host only: command that opens a shell on another host (default `ssh`) |
| `NIX_DEPLOY_ENTER` | prefix that runs a command inside the same store view on whichever host it runs on |
| `NIX_DEPLOY_DEADLINE` | epoch seconds when the lease ends, when it has a walltime: checkpoint and exit before |

stdout and stderr go to `process.log` in the workdir. The program stays
foreground in its own process group; `stop` sends SIGTERM to that group.

## Machine configuration

What a provider *name* means is configured per machine, in
`~/.config/nixsci/providers.json`, a file Nix builds with `builtins.toJSON`. Callers say `provider = "lab"` and never
care how:

```nix
pkgs.writeText "providers.json" (builtins.toJSON { providers.lab = {
  use = "static";                  # hosts you reserved yourself, reached over ssh
  user = "me";
  jump = "me@gateway";             # ProxyJump chain, optional
  # workdir defaults to /tmp/<user>-nix-deploy: private store, runs and shipped nix live there
  # the patched static Nix helper is embedded in the Nix-built nixsci package
  ssh_options = [ "-o" "StrictHostKeyChecking=accept-new" ];
  ready_timeout = 180;             # hosts that just booted may refuse ssh for a while
  # ssh_command = [ "oarsh" ];     # a site's own ssh wrapper; scp_command, rsh likewise
}; })
```

The hosts themselves come with each lease (`--opt hosts=a,b` or `hosts = [...]` in
the spec's `[resources.opts]`), so one entry serves every reservation. On a server
with a shared-credential broker, the same name can instead say
`use = "<installed plugin>"`. Code above the provider layer does not change.

A machine-wide file, `/etc/nixsci/providers.json`, has the same format and
fills in names the user's own file does not set; the user's file wins.

**The shipped Nix belongs to nixsci.** Hosts that have no Nix get the static Nix 2.34.8
binary embedded in the Nix-built `nixsci` package. Its source revision is fixed by
`flake.lock`, and its namespace-root patch is in `deploy/nix-namespace-root.patch`.
The controller hashes that binary before shipping it; any explicit bootstrap path must
match the embedded binary byte for byte.

**The ssh layer is configuration, not code.** Per target (or per provider default):
`ssh_command` and `scp_command` (default `ssh` and `scp -q`), `ssh_options` (extra
`-o ...`), `jump` (ProxyJump chain), `rsh` (what jobs use to reach their peers,
becomes `NIX_DEPLOY_RSH`) and `ready_timeout`. That is how Grid'5000 (a gateway
plus `oarsh` or plain ssh), a cloud VM or a laptop on the LAN are all
different values.

**Walltime.** `walltime` (minutes) is a generic resource. When given, the lease
records when it ends: `nixsci deploy run` refuses an expired lease, jobs get
`NIX_DEPLOY_DEADLINE`, and the lab executor stops waiting for jobs at the deadline.

Generic resources are the same for every provider: `hosts`, `gpus`, `walltime`
(minutes), `system`. **Everything provider-specific goes in `opts`**, and each
provider rejects options it does not know. Putting `site` among the generic
resources is an error.

## Command line

```sh
nixsci deploy lease acquire lab warm --walltime 240 --opt hosts=n1,n2,n3,n4   # hosts you reserved
nixsci deploy lease ls
nixsci deploy lease show warm
nixsci deploy --lease warm run . my-experiment run1 --handle run1.json --arg 8 --env SEED=3
nixsci deploy --lease warm status run1.json
nixsci deploy --lease warm fetch run1.json out/ ./results
nixsci deploy --lease warm stop run1.json
nixsci deploy lease release warm
```

A lease outlives the process that acquired it (state is under
`~/.local/state/nix-deploy/leases/`), so a warm allocation can be used by many
later commands. `--config FILE --target NAME` addresses one static target from
a JSON file instead of a lease.

The file is `builtins.toJSON` of a value like this one:

```nix
{ targets.box = {
  backend = "ssh";
  host = "user@host";
  system = "x86_64-linux";
  store = "/tmp/user/store";         # private store root on the host
  run_root = "/tmp/user/runs";
  rootless = true;                    # required; false is rejected
  remote_bootstrap = "/tmp/user/bin/nix";
  ssh_options = [ "-o" "ProxyJump=user@jump" ];
}; }
```

`backend = "native"` also uses a private store. The `local` provider places it
beside the run directories under the user's state directory; `/nix/store` is never
a compute-target store.

## Python API

```python
from nixsci.deploy import providers, factory, resolver, group, leases

lease = providers.acquire("lab", {"walltime": 240}, {"hosts": "n1,n2"})   # or leases.load("warm")
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
lives in its own package and nixsci.deploy never names it:

```toml
[project.entry-points."nixsci.deploy.providers"]
my-site = "my_site.provider:Provider"
[project.entry-points."nixsci.deploy.backends"]
my-backend = "my_site.backend:Backend"
```

A provider has `acquire(resources, opts) -> Lease` and `release(state)`; the
`Lease` carries `targets` (backend configs, one per host), `hosts` (names
reachable from inside the allocation) and a JSON-able `state` that `release`
needs, so a lease can be released from another process. A backend takes its
config dict and implements the methods above. See `nixsci.deploy/providers.py`
(`Local`, `Static`) and `nixsci.deploy/ssh.py`.

## Testing

```sh
PYTHONPATH=. python3 tests/e2e.py local 1       # resolve, stage, run, read the log
PYTHONPATH=. python3 tests/e2e.py lab --opt hosts=n1,n2   # same on hosts you reserved yourself
```

The flake's `experiments.<system>.hello` prints where it ran, which hosts it
saw, and its `NIX_DEPLOY_ENTER`. The tree must be committed first.

## Status

Earlier runs exercised transfer and lifecycle behavior but did not test the
namespace-root and nested-user-namespace contract in this branch. They do not
establish this claim. `.tmp/rootless-experiments/REPORT.md` records the branch's
host results; a pass requires the NAR map, namespace evidence, denial checks, and
teardown, not merely a successful command exit.
