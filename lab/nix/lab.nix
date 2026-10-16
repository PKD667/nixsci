# The lab as Nix values: experiments, and analyses that are derivations over locked runs.
#
#   lab = nixsci.lib.lab { inherit pkgs; project = self; };
#   lab.experiment { name = "demo"; closures.${system} = ...; seeds = [ 0 1 ]; ... }
#   An experiment with no `closures` is recorded by hand (`lab.Run`); it has no `run` to execute.
#   lab.analysis   { name = "curves"; use.demo = demo; pipelines.curve.script = ./curve.R; }
#
# An experiment is the one impure step: its `run` app starts the Python executor, which runs the
# jobs on hosts, seals each finished run and adds it to the Nix store. The runs it produced are
# listed in `lab.lock` next to the flake. Everything after that is pure: an analysis is built
# from those run store paths in the sandbox, with no network, and sees only the data it `use`s.
{ pkgs, nixsci, python, rEnv, project }:
let
  inherit (pkgs) lib;
  host = pkgs.stdenv.hostPlatform.system;
  name' = what: n:
    assert lib.assertMsg (builtins.match "[A-Za-z][A-Za-z0-9_.-]*" n != null)
      "${what} name ${builtins.toJSON n} must start with a letter and use letters, digits, '_', '.', '-'";
    n;

  lockFile = project + "/lab.lock";
  locked = if builtins.pathExists lockFile then builtins.fromJSON (builtins.readFile lockFile) else { };

  # The runs of an experiment that its lock names, each checked by the hash of its contents and
  # against the code. A run whose closure is not one of the experiment's closures now measured
  # other code: it is stale. A run with no closure was recorded by hand: it is untraced.
  runsOf = exp:
    let
      entries = locked.${exp.name} or [ ];
      current = map toString (lib.attrValues exp.closures);
    in
    assert lib.assertMsg (entries != [ ])
      "experiment ${exp.name} has no locked runs: run it (nix run .#${exp.name}) and commit lab.lock";
    map
      (r:
        let
          path = builtins.path { inherit (r) path sha256 name; };
          closure = (builtins.fromJSON (builtins.readFile "${path}/manifest.json")).closure or null;
        in
        { inherit (r) name; inherit path; traced = closure != null; stale = closure != null && !(builtins.elem closure current); })
      entries;

  # Every locked run of one experiment as the Parquet tables it declared, plus `runs.json`.
  # One derivation per experiment, not per run: see compact.py for the cost that avoids.
  compactRuns = exp: runs:
    pkgs.runCommand "data-${exp.name}" { nativeBuildInputs = [ python ]; } ''
      python -m nixsci.lab.compact $out ${lib.concatMapStringsSep " " (r: "${r.path}") runs}
    '';

  # The experiment's derivations, as a dependency of the view: the .drv, not its output, so the
  # code that produced the runs is in the analysis's derivation graph and nothing is rebuilt.
  codeOf = exp: builtins.toJSON (lib.mapAttrs
    (_: c: if c ? drvPath then builtins.unsafeDiscardOutputDependency c.drvPath else toString c)
    exp.closures);
in
rec {
  experiment =
    { name
    , closures ? { }
    , systems ? builtins.attrNames closures
    , seeds ? [ ]
    , replicates ? 1
    , params ? { }
    , sweep ? { }
    , resources ? { }
    , outputs ? { }
    , data ? { }
    }:
    let
      spec = { kind = "experiment"; name = name' "experiment" name; inherit systems seeds replicates params sweep resources outputs data; };
      specFile = pkgs.writeText "${name}-spec.json" (builtins.toJSON spec);
      source = builtins.toJSON { rev = project.rev or project.dirtyRev or null; narHash = project.narHash or null; };
    in
    {
      inherit name spec specFile closures;
      run = {
        type = "app";
        program = toString (pkgs.writeShellScript "run-${name}" ''
          exec ${nixsci}/bin/nixsci lab exec --spec ${specFile} \
            ${lib.concatMapStringsSep " " (s: "--closure ${s}=${closures.${s}}") systems} \
            --source ${lib.escapeShellArg source} "$@"
        '');
      };
    };

  # `allowStale = true` builds over stale runs with a warning: a draft. Publishing refuses it, and
  # refuses untraced runs; both lists are on the result.
  analysis = { name, use, pipelines, allowStale ? false }:
    let
      n = name' "analysis" name;
      runs = lib.mapAttrs (_: runsOf) use;
      named = pick: lib.concatLists (lib.mapAttrsToList
        (alias: rs: map (r: "${alias}/${r.name}") (lib.filter pick rs)) runs);
      stale = named (r: r.stale);
      untraced = named (r: !r.traced);
      staleMsg = "analysis ${n}: ${toString (lib.length stale)} locked run(s) measured code that is "
        + "not the experiment's closure any more: ${lib.concatStringsSep ", " stale}. "
        + "Run the experiment again and commit lab.lock";
      view = pkgs.runCommand "view-${n}" { } ''
        shopt -s nullglob
        mkdir $out
        ${lib.concatStrings (lib.mapAttrsToList (alias: exp:
          let t = compactRuns exp runs.${alias};
          in ''
            mkdir -p $out/${alias}
            for dir in ${t}/*/; do
              table=$(basename "$dir"); mkdir -p $out/${alias}/$table
              ln -s "$dir"*.parquet $out/${alias}/$table/
            done
            cp ${t}/runs.json $out/${alias}/runs.json
            echo ${lib.escapeShellArg (codeOf exp)} > $out/${alias}/code.json
          '') use)}
      '';
      runPipeline = pname: p:
        pkgs.runCommand "${n}-${pname}" { nativeBuildInputs = [ rEnv ]; } ''
          export HOME=$TMPDIR NIX_LAB_VIEW=${view} NIX_LAB_OUT=$out
          mkdir $out
          cd $out
          Rscript ${p.script}
        '';
      built = lib.mapAttrs runPipeline pipelines;
      result = (pkgs.symlinkJoin { name = n; paths = lib.attrValues built; })
        // { inherit view stale untraced; pipelines = built; };
    in
    if stale == [ ] then result
    else if allowStale then lib.warn "${staleMsg}, or keep this draft (allowStale)." result
    else throw "${staleMsg}, or pass allowStale = true for a draft.";
}
