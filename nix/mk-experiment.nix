# Produces a derivation shaped for nix-deploy: experiment.json at the root, a
# `bin/run` wrapper that provisions $NIX_LAB_DIR, runs the program, forwards
# SIGTERM, and records status.json however the program was written.
{ pkgs }:
{ name, program, runtime ? [ ], metadata ? { }, resources ? { }, env ? { } }:
pkgs.runCommand "experiment-${name}" {
  nativeBuildInputs = [ pkgs.jq ];
  inherit program;
  runtimePath = pkgs.lib.makeBinPath runtime;
  meta_json = builtins.toJSON metadata;
  resources_json = builtins.toJSON resources;
  env_json = builtins.toJSON env;
} ''
  mkdir -p $out/bin
  cat > $out/bin/run <<WRAP
  #!${pkgs.runtimeShell}
  set -u
  : "\''${NIX_DEPLOY_WORKDIR:=\$PWD}"
  export NIX_LAB_DIR="\''${NIX_LAB_DIR:-\$NIX_DEPLOY_WORKDIR/lab}"
  mkdir -p "\$NIX_LAB_DIR/artifacts"
  export PATH="$runtimePath:\$PATH"
  started=\$(${pkgs.coreutils}/bin/date -u +%Y-%m-%dT%H:%M:%SZ)
  "$program" "\$@" &
  child=\$!
  trap 'kill -TERM \$child 2>/dev/null' TERM INT
  code=0
  wait \$child || code=\$?
  # a trapped signal interrupts the first wait; wait again for the real status
  if kill -0 \$child 2>/dev/null; then wait \$child || code=\$?; fi
  ended=\$(${pkgs.coreutils}/bin/date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"exit_code":%s,"started":"%s","ended":"%s"}\n' "\$code" "\$started" "\$ended" > "\$NIX_LAB_DIR/status.json"
  exit \$code
  WRAP
  chmod +x $out/bin/run
  jq -n --arg program "$out/bin/run" --arg name "${name}" \
        --argjson metadata "$meta_json" --argjson resources "$resources_json" \
        --argjson env "$env_json" \
    '{program: $program, metadata: ($metadata + {lab: {format: 1, name: $name}}),
      resources: $resources, env: $env}' > $out/experiment.json
''
