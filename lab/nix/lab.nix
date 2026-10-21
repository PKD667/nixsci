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
{ pkgs, nixsci, python, rEnv, project, lockedOverride ? null }:
let
  inherit (pkgs) lib;
  host = pkgs.stdenv.hostPlatform.system;
  name' = what: n:
    assert lib.assertMsg (builtins.match "[A-Za-z][A-Za-z0-9_.-]*" n != null)
      "${what} name ${builtins.toJSON n} must start with a letter and use letters, digits, '_', '.', '-'";
    n;

  lockFile = project + "/lab.lock";
  rawLock = if builtins.pathExists lockFile then builtins.fromJSON (builtins.readFile lockFile) else { };
  locked = if lockedOverride != null then lockedOverride else if rawLock ? format then
    assert lib.assertMsg (lib.head (lib.splitString "." rawLock.format) == "2") "unsupported lab.lock format ${rawLock.format}";
    rawLock.runs else rawLock;
  fetchRun = import ./fetch-run.nix { inherit pkgs; };

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
          path = fetchRun r;
          # v2 facts are checked against the sealed manifest during compaction. v1 locks have no
          # facts: fetch the old run once to read its manifest (legacy import-from-derivation).
          m = r.facts or (builtins.fromJSON (builtins.readFile "${path}/manifest.json"));
          closure = m.closure or null;
          job = { seed = m.seed or null; params = m.params or { }; };
        in
        {
          inherit (r) name; inherit path job; facts = r.facts or null;
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
      python -m nixsci.lab.compact $out ${lib.concatMapStringsSep " " (r: "${r.path}") runs} \
        --expect ${pkgs.writeText "${exp.name}-expected.json" (builtins.toJSON (builtins.listToAttrs
          (map (r: { name = r.name; value = r.facts; }) runs)))}
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
      inherit name spec specFile closures source;
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
  analysis = args@{ name, use, pipelines, draft ? false, claims ? { }, confidence ? 0.95 }:
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
        (lib.optional (stale != [ ]) "Runs of older code: ${lib.concatStringsSep ", " stale}")
        (lib.optional (orphan != [ ]) "Runs of no job in the spec: ${lib.concatStringsSep ", " orphan}")
        (lib.optional (missing != [ ]) "Jobs without their runs: ${lib.concatStringsSep "; " missing}")
        (lib.optional (untraced != [ ]) "Runs recorded by hand: ${lib.concatStringsSep ", " untraced}")
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
        let
          language = p.language or (if lib.hasSuffix ".py" (toString p.script) then "python" else "r");
          runner = if language == "r" then "Rscript" else if language == "python" then "python"
            else throw "analysis ${n}/${pname}: language must be r or python";
          environment = if language == "r" then rEnv else python;
        in pkgs.runCommand "${n}-${pname}" { nativeBuildInputs = [ environment ] ++ (p.packages or [ ]); } ''
          export HOME=$TMPDIR NIX_LAB_VIEW=${view} NIX_LAB_OUT=$out
          export NIXSCI_VIEW=$NIX_LAB_VIEW NIXSCI_OUT=$NIX_LAB_OUT
          mkdir $out
          cd $out
          ${runner} ${p.script}
        '';
      claimFile = pkgs.writeText "${n}-claims.json" (builtins.toJSON { format = "2.0"; inherit claims; });
      claimReport = pkgs.runCommand "${n}-claims" { nativeBuildInputs = [ python ]; } ''
        mkdir $out
        cp ${claimFile} $out/claims.json
        # A report is data, not a build gate: unsupported/inconclusive claims must remain visible.
        python -m nixsci.stats.claims ${claimFile} --view ${view} --level ${toString confidence} \
          --json $out/claims-report.json || { rc=$?; [ "$rc" = 1 ] || [ "$rc" = 3 ]; }
      '';
      built = lib.mapAttrs runPipeline pipelines;
      result = (pkgs.symlinkJoin { name = n; paths = lib.attrValues built ++ lib.optional (claims != { }) claimReport; })
        // {
          inherit view clean stale orphan missing untraced claimFile claimReport;
          pipelines = built;
          withLock = newLock: level:
            (import ./lab.nix { inherit pkgs nixsci python rEnv project; lockedOverride = newLock; })
              .analysis (args // { confidence = level; });
          remeasureSpec = lib.mapAttrs (_: exp: {
            inherit (exp) name specFile closures source;
            inherit claimFile;
            view = toString view;
          }) use;
          # What nixsci doc reads: one list, each entry saying why.
          unclean = map (r: "${r}: older code") stale ++ map (r: "${r}: no job of the spec") orphan
            ++ map (j: "${j}: no run") missing ++ map (r: "${r}: recorded by hand") untraced;
        };
    in
    assert lib.assertMsg (confidence > 0.5 && confidence < 1.0) "confidence must lie between 0.5 and 1";
    if clean then result
    else if draft then lib.warn "${report}; built as a draft." result
    else throw "${report}, or pass draft = true.";
}
