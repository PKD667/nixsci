# Produces a derivation shaped for nix-deploy: experiment.json at the root, a
# `bin/run` wrapper that provisions $NIX_LAB_DIR, runs the program, forwards
# SIGTERM, and records status.json however the program was written.
{ pkgs }:
{ name, program, args ? [ ], runtime ? [ ], metadata ? { }, resources ? { }, env ? { } }:
pkgs.runCommand "experiment-${name}" {
  nativeBuildInputs = [ pkgs.jq ];
  inherit program;
  programArgs = pkgs.lib.escapeShellArgs args;
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
  # facts about the host, for comparing measurements (SPEC.md: machine.json)
  cpu=""; while IFS=: read -r k v; do case "\$k" in "model name"*) cpu=\''${v# }; break;; esac; done < /proc/cpuinfo 2>/dev/null
  mem=null; while read -r k v _; do case "\$k" in MemTotal:) mem=\$v; break;; esac; done < /proc/meminfo 2>/dev/null
  printf '{"hostname":"%s","kernel":"%s","arch":"%s","cpus":%s,"cpu":"%s","mem_kb":%s}\n' \
    "\$(${pkgs.coreutils}/bin/uname -n)" "\$(${pkgs.coreutils}/bin/uname -r)" "\$(${pkgs.coreutils}/bin/uname -m)" \
    "\$(${pkgs.coreutils}/bin/nproc)" "\''${cpu//[\"\\\\]/}" "\$mem" > "\$NIX_LAB_DIR/machine.json"
  "$program" $programArgs "\$@" &
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
    '{program: $program, metadata: ($metadata + {lab: {name: $name}}),
      resources: $resources, env: $env}' > $out/experiment.json
''
