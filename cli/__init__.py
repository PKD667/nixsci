"""The one command: `nixsci <command> [args]`.

Commands are the entry points of the group `nixsci.commands`, so a package that depends on
nixsci adds its own (`nixsci train ...`) by declaring one; nothing here knows the list.
"""

from __future__ import annotations

import sys
from importlib.metadata import entry_points


def installed():
    return {ep.name: (lambda argv, ep=ep: ep.load()(argv)) for ep in entry_points(group="nixsci.commands")}


def main(argv=None, commands=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = installed() if commands is None else commands
    listing = ", ".join(sorted(commands))
    if not argv or argv[0] in ("-h", "--help"):
        print(f"usage: nixsci <command> [args]\ncommands: {listing}")
        return 0 if argv else 2
    name, *rest = argv
    if name not in commands:
        print(f"nixsci: no command {name!r}. Commands: {listing}", file=sys.stderr)
        return 2
    return commands[name](rest) or 0
