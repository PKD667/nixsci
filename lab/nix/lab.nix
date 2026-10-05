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

  # The runs of an experiment that its lock names, each checked by the hash of its contents.
  runsOf = exp:
    let entries = locked.${exp.name} or [ ];
    in assert lib.assertMsg (entries != [ ])
      "experiment ${exp.name} has no locked runs: run it (nix run .#${exp.name}) and commit lab.lock";
    map (r: { inherit (r) name; path = builtins.path { inherit (r) path sha256 name; }; }) entries;

  # One run as the Parquet tables it declared, plus its manifest row.
  compactRun = run:
    pkgs.runCommand "data-${run.name}" { nativeBuildInputs = [ python ]; } ''
      python -m nixsci.lab.compact ${run.path} $out
    '';
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

  analysis = { name, use, pipelines }:
    let
      n = name' "analysis" name;
      view = pkgs.runCommand "view-${n}" { nativeBuildInputs = [ pkgs.jq ]; } ''
        shopt -s nullglob
        mkdir $out
        ${lib.concatStrings (lib.mapAttrsToList (alias: exp:
          let tables = map compactRun (runsOf exp);
          in ''
            mkdir -p $out/${alias}
            ${lib.concatMapStrings (t: ''
              for dir in ${t}/*/; do
                table=$(basename "$dir"); mkdir -p $out/${alias}/$table
                ln -s "$dir"*.parquet $out/${alias}/$table/
              done
            '') tables}
            jq -s . ${lib.concatMapStringsSep " " (t: "${t}/row.json") tables} > $out/${alias}/runs.json
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
    in
    (pkgs.symlinkJoin { name = n; paths = lib.attrValues built; }) // { inherit view; pipelines = built; };
}
