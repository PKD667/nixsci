# A deployment closure is executable policy, not a recorded measurement.
{ pkgs }:
{ name, program, args ? [ ], runtime ? [ ], env ? { }, resources ? { }, walltime ? null, restart ? { }, metadata ? { }, commands ? { } }:
let
  policy = restart.policy or "no";
  maximum = restart.max or null;
  delay = restart.delay or 0;
  duration = if walltime == null then null else toString (walltime * 60);
  runtimePath = pkgs.lib.makeBinPath (runtime ++ [ pkgs.coreutils ]);
  programArgs = pkgs.lib.escapeShellArgs args;
  restartPolicy = policy;
  restartMaximum = if maximum == null then "" else toString maximum;
  restartDelay = toString delay;
  manifest = builtins.toJSON {
    format = "2.0"; inherit name program resources walltime metadata commands;
    args = args; inherit env;
    restart = { inherit policy delay; max = maximum; };
  };
in
assert builtins.isString name;
assert builtins.isString program;
assert builtins.isList args && builtins.all builtins.isString args;
assert builtins.isAttrs env && builtins.isAttrs resources && builtins.isAttrs metadata && builtins.isAttrs commands;
assert walltime == null || (builtins.isFloat walltime || builtins.isInt walltime) && walltime > 0;
assert builtins.elem policy [ "no" "on-failure" "always" ];
assert maximum == null || builtins.isInt maximum && maximum >= 0;
assert (builtins.isFloat delay || builtins.isInt delay) && delay >= 0;
pkgs.runCommand "deployment-${name}" {
  nativeBuildInputs = [ pkgs.jq ];
  inherit duration;
} ''
  mkdir -p $out/bin
  cat > $out/bin/run <<'WRAP'
  #!${pkgs.runtimeShell}
  set -u
  : "''${NIX_DEPLOY_WORKDIR:=$PWD}"
  export PATH="${runtimePath}:$PATH"
  mkdir -p "$NIX_DEPLOY_WORKDIR"
  attempts="$NIX_DEPLOY_WORKDIR/attempts.jsonl"
  : >> "$attempts"
  child=
  stopping=0
  stop() { stopping=1; if [ -n "$child" ]; then kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || :; fi; }
  trap stop TERM INT HUP
  run_loop() {
    n=0
    while :; do
      n=$((n + 1))
      started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      setsid "${program}" ${programArgs} "$@" &
      child=$!
      code=0
      wait "$child" || code=$?
      if kill -0 "$child" 2>/dev/null; then wait "$child" || code=$?; fi
      child=
      ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      printf '{"started":"%s","ended":"%s","exit_code":%s}\n' "$started" "$ended" "$code" >> "$attempts"
      [ "$stopping" = 0 ] || return "$code"
      case "${restartPolicy}" in
        no) return "$code" ;;
        on-failure) [ "$code" -ne 0 ] || return 0 ;;
        always) ;;
      esac
      if [ -n "${restartMaximum}" ] && [ "$n" -gt "${restartMaximum}" ]; then return "$code"; fi
      sleep "${restartDelay}" & child=$!; wait "$child" || :; child=
      [ "$stopping" = 0 ] || return 143
    done
  }
  if [ "''${1:-}" = --nix-deploy-internal ]; then shift; run_loop "$@"; exit $?; fi
  ${if duration == null then "run_loop \"$@\"" else ''exec ${pkgs.coreutils}/bin/timeout --signal=TERM --kill-after=30s ${duration}s "$0" --nix-deploy-internal "$@"''}
  WRAP
  chmod +x $out/bin/run
  printf '%s\n' "$manifest" > $out/deployment.json
''
