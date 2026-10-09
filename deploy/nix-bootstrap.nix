{ pkgs }:
# A diverted store must be a namespace root, not a chroot, so payloads can
# create a further unprivileged user namespace for isolated execution.
assert pkgs.pkgsStatic.nix.version == "2.34.8";
pkgs.pkgsStatic.nix.overrideScope (_final: prev: {
  nix-cli = prev.nix-cli.overrideAttrs (old: {
    patches = (old.patches or []) ++ [ ./nix-namespace-root.patch ];
  });
  # Nix 2.34.8's musl close_range branch lacks its declaring header.
  nix-util = prev.nix-util.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      substituteInPlace unix/file-descriptor.cc \
        --replace-fail '#include <span>' $'#include <span>\n#include <sys/syscall.h>'
    '';
  });
})
