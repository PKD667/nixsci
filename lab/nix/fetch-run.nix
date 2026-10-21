# A run is a content-addressed Nix path. Its name and recursive SHA-256 derive its store path,
# regardless of which machine measured it. A configured substituter supplies the bytes; `lab pull`
# can prefill the local store from any peer when that peer is not configured as a substituter.
{ pkgs }:
{ name, sha256, ... }:
pkgs.runCommand name {
  outputHash = sha256;
  outputHashMode = "recursive";
  outputHashAlgo = "sha256";
  preferLocalBuild = false;
  allowSubstitutes = true;
} ''
  echo ${pkgs.lib.escapeShellArg "nixsci: run ${name} (${sha256}) is not available locally or from configured substituters. Run nixsci lab pull [STORE-URL] in the code project, then build again."} >&2
  exit 1
''
