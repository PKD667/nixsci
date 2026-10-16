# Produces a lab experiment closure in the deployment.json v2 format, while
# retaining lab-specific status.json and machine.json semantics.
# `bin/run` wrapper that provisions $NIX_LAB_DIR, runs the program, forwards
# SIGTERM, and records status.json however the program was written.
{ pkgs }:
{ name, program, args ? [ ], runtime ? [ ], metadata ? { }, resources ? { }, env ? { }, hardware ? { } }:
assert builtins.isAttrs hardware;
assert builtins.all (key: builtins.elem key [ "cpu" "arch" "cores" ]) (builtins.attrNames hardware);
assert (!hardware ? cpu || builtins.isString hardware.cpu);
assert (!hardware ? arch || builtins.isString hardware.arch);
assert (!hardware ? cores || builtins.isInt hardware.cores && hardware.cores > 0);
let
  hardwareCheck = pkgs.lib.concatStringsSep "\n" (pkgs.lib.mapAttrsToList (key: expected: ''
    ${({
      cpu = ''cpu=""; while IFS=: read -r k v; do case "$k" in "model name"*) v="''${v#"''${v%%[![:space:]]*}"}"; cpu="$v"; break;; esac; done < /proc/cpuinfo 2>/dev/null; actual="$cpu"'';
      arch = ''arch=$(uname -m); actual="$arch"'';
      cores = ''cores=$(nproc); actual="$cores"'';
    }).${key}}
    if [ "$actual" != ${pkgs.lib.escapeShellArg (toString expected)} ]; then
      printf 'hardware mismatch: ${key} expected %s, got %s\n' ${pkgs.lib.escapeShellArg (toString expected)} "$actual" >&2
      exit 78
    fi
  '') hardware);
in
pkgs.runCommand "experiment-${name}" {
  nativeBuildInputs = [ pkgs.jq ];
  inherit program;
  programArgs = pkgs.lib.escapeShellArgs args;
  runtimePath = pkgs.lib.makeBinPath (runtime ++ [ pkgs.coreutils pkgs.util-linux ]);
  meta_json = builtins.toJSON metadata;
  resources_json = builtins.toJSON resources;
  env_json = builtins.toJSON env;
  hardware_json = builtins.toJSON hardware;
} ''
  mkdir -p $out/bin
  cat > $out/bin/run <<WRAP
  #!${pkgs.runtimeShell}
  set -u
  export PATH="$runtimePath:\$PATH"
  ${hardwareCheck}
  : "\''${NIX_DEPLOY_WORKDIR:=\$PWD}"
  export NIX_LAB_DIR="\''${NIX_LAB_DIR:-\$NIX_DEPLOY_WORKDIR/lab}"
  mkdir -p "\$NIX_LAB_DIR/artifacts"
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
        --argjson env "$env_json" --argjson hardware "$hardware_json" \
    '{format: "2.0", name: $name, program: $program, args: [], env: $env,
      resources: $resources, hardware: $hardware, walltime: null, restart: {policy: "no", max: 0, delay: 0},
      metadata: ($metadata + {lab: {name: $name}})}' > $out/deployment.json
''
