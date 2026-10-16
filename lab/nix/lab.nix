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
  # against the experiment as it is now. A run of another closure measured other code: stale. A
  # run whose seed and parameters are no job of the spec: orphan. A run with no closure was
  # recorded by hand: untraced.
  runsOf = exp:
    let
      entries = locked.${exp.name} or [ ];
      current = map toString (lib.attrValues exp.closures);
      jobs = map (j: { inherit (j) seed params; }) (jobsOf exp.spec);
    in
    assert lib.assertMsg (entries != [ ])
      "experiment ${exp.name} has no locked runs: run it (nix run .#${exp.name}) and commit lab.lock";
    map
      (r:
        let
          path = builtins.path { inherit (r) path sha256 name; };
          m = builtins.fromJSON (builtins.readFile "${path}/manifest.json");
          closure = m.closure or null;
          job = { seed = m.seed or null; params = m.params or { }; };
        in
        {
          inherit (r) name; inherit path job;
          traced = closure != null;
          stale = closure != null && !(builtins.elem closure current);
          orphan = closure != null && !(builtins.elem job jobs);
        })
      entries;

  # The jobs a spec expands to, as spec.py's Spec.jobs: every sweep point times every seed.
  jobsOf = spec:
    let points = if spec.sweep == { } then [ { } ] else lib.cartesianProduct spec.sweep;
    in lib.concatMap (point: map (seed: { inherit seed; params = spec.params // point; })
      (if spec.seeds == [ ] then [ null ] else spec.seeds)) points;

  # Jobs of the spec with fewer finished runs of the current code than `replicates` asks for.
  missingOf = exp: runs:
    let fresh = lib.filter (r: r.traced && !r.stale) runs;
    in lib.optionals (exp.closures != { }) (lib.filter
      (job: lib.count (r: r.job == job) fresh < exp.spec.replicates)
      (jobsOf exp.spec));

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

  # An analysis is clean when every locked run measured the experiment's current code and spec,
  # every job of the spec has its runs, and nothing was recorded by hand. Anything else fails
  # evaluation unless `draft = true`, which builds with a warning. `clean` and the four lists are
  # on the result, for a publish step to refuse.
  analysis = { name, use, pipelines, draft ? false }:
    let
      n = name' "analysis" name;
      runs = lib.mapAttrs (_: runsOf) use;
      named = pick: lib.concatLists (lib.mapAttrsToList
        (alias: rs: map (r: "${alias}/${r.name}") (lib.filter pick rs)) runs);
      stale = named (r: r.stale);
      orphan = named (r: r.orphan && !r.stale);
      untraced = named (r: !r.traced);
      missing = lib.concatLists (lib.mapAttrsToList
        (alias: exp: map (j: "${alias} seed=${builtins.toJSON j.seed} params=${builtins.toJSON j.params}")
          (missingOf exp runs.${alias})) use);
      problems = lib.concatLists [
        (lib.optional (stale != [ ]) "measured older code: ${lib.concatStringsSep ", " stale}")
        (lib.optional (orphan != [ ]) "are no job of the spec: ${lib.concatStringsSep ", " orphan}")
        (lib.optional (missing != [ ]) "jobs without their runs: ${lib.concatStringsSep "; " missing}")
        (lib.optional (untraced != [ ]) "recorded by hand: ${lib.concatStringsSep ", " untraced}")
      ];
      clean = problems == [ ];
      report = "analysis ${n} is not clean. ${lib.concatStringsSep ". " problems}. "
        + "Run the experiments (nix run .#<experiment>) and commit lab.lock";
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
        // { inherit view clean stale orphan missing untraced; pipelines = built; };
    in
    if clean then result
    else if draft then lib.warn "${report}; built as a draft." result
    else throw "${report}, or pass draft = true.";
}
