# The Nix every rootless target runs: the Nix project's own static build, with one patch.
#
# `nix` is the Nix project's flake input (pinned in flake.lock, with its own nixpkgs), not the caller's nixpkgs: its
# Hydra builds and uploads packages.<system>.nix-cli-static and every component under it, so a clean store fetches
# them (about 200 MiB) and compiles only the patched nix-cli. nixpkgs' pkgsStatic.nix is built by nobody, and
# compiling all of Nix on the first deploy cost about 40 minutes and exhausted a 15 GB machine.
#
# The patch: a diverted store must be a namespace root, not a chroot, so payloads can create a further
# unprivileged user namespace for isolated execution.
{ pkgs, nix }:
nix.packages.${pkgs.stdenv.hostPlatform.system}.nix-cli-static.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./nix-namespace-root.patch ];
})
