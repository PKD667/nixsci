"""Ad-hoc hosts: run an experiment on machines you can already `ssh` to, with no provider file.

    nixsci lab exec --hosts a,b ...        or      NIXSCI_HOSTS=a,b nix run .#<experiment>

The names are ssh host names (aliases from ~/.ssh/config are fine). Nothing else is configured: the
user name comes from `ssh -G <first host>`, and ssh itself applies the rest of the configuration (port,
jump host, identity) to each name. The hosts become an in-memory static lease, exactly as a `static`
provider with `hosts` would give, and the caller gets it back from `lease`.

Before anything is shipped, the first host is probed with `ssh -o BatchMode=yes <host> true`: a host that
would ask for a password fails here, with the ssh error, and not in the middle of a copy.
"""

from __future__ import annotations

import os
import subprocess
from typing import Mapping

from .providers import Lease, Resources, Static

SSH = ("ssh",)


class AdhocError(RuntimeError):
    """A message for the user, not a traceback: the caller prints it and stops."""


def parse_hosts(value: str) -> list[str]:
    names = [h.strip() for h in value.split(",") if h.strip()]
    repeated = sorted({h for h in names if names.count(h) > 1})
    if repeated:
        raise ValueError(f"host repeated: {', '.join(repeated)}")
    return names


def hosts_from(value: str | None, environ: Mapping[str, str] = os.environ) -> list[str] | None:
    """The ad-hoc hosts the command line or NIXSCI_HOSTS names, or None when neither does."""
    text = value if value is not None else environ.get("NIXSCI_HOSTS", "")
    return parse_hosts(text) or None


def ssh_config(host: str, ssh=SSH) -> dict[str, str]:
    """What `ssh -G HOST` resolves for HOST: lower-case keys, e.g. user, hostname, port."""
    result = subprocess.run([*ssh, "-G", host], capture_output=True, text=True)
    if result.returncode:
        raise AdhocError(f"cannot read the ssh configuration of {host}: {_last_line(result, host)}")
    config = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key and key not in config:
            config[key.lower()] = value.strip()
    return config


def preflight(host: str, ssh=SSH) -> None:
    """Prove that `host` accepts ssh without a password (or a passphrase prompt)."""
    result = subprocess.run([*ssh, "-o", "BatchMode=yes", host, "true"], capture_output=True, text=True)
    if result.returncode:
        raise AdhocError(
            f"cannot ssh to {host} without a password: {_last_line(result, host).rstrip('.')}. "
            "Check ~/.ssh/config and ssh-agent."
        )


def _last_line(result: subprocess.CompletedProcess, host: str) -> str:
    lines = (result.stderr or "").strip().splitlines()
    return lines[-1] if lines else f"exit status {result.returncode}"


def lease(hosts: list[str], resources: Mapping, ssh=SSH) -> Lease:
    """A static lease on `hosts`, all of them, in this order (the first host starts a multi-host job).

    `resources` are the spec's; its `hosts` count is replaced by the number of hosts given."""
    if not hosts:
        raise AdhocError("give at least one host")
    preflight(hosts[0], ssh)
    user = ssh_config(hosts[0], ssh).get("user")
    if not user:
        raise AdhocError(f"ssh -G {hosts[0]} gives no user name")
    # The same absolute path on every host (one run_root per job); each host's own user owns it.
    workdir = f"/tmp/{user}-nix-deploy"
    res = Resources.of({**resources, "hosts": len(hosts)})
    return Static().acquire(res, {"hosts": list(hosts), "workdir": workdir})


__all__ = ["AdhocError", "hosts_from", "lease", "parse_hosts", "preflight", "ssh_config"]
