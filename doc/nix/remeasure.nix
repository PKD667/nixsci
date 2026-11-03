# What a document needs to be measured again: the lab analyses its projects read, and the plan that
# reruns each one's experiments. `nixsci doc remeasure` reads `measurementPlan`, and builds the PDF again
# from fresh locks through `withLock`; see doc/remeasure.py.
#
#   remeasure { name; projects; remeasurement; } -> { analysisDrvs; measurementPlan; marker; }
#   analysisDrvs     the lab analyses among the inputs, by derivation path
#   measurementPlan  JSON: one entry per experiment to run, naming its analysis
#   marker           the TeX file that marks every page REMEASURED, or null for an ordinary build
{ pkgs, lib, provenance }:
{ name, projects, remeasurement }:
let
  analysisEntries = provenance.inputsOf projects;
  analysisDrvs = lib.foldl' (acc: e:
    let key = builtins.unsafeDiscardStringContext (toString (e.drv or null));
    in if (e.drv or null) == null || !(e.drv ? withLock) || acc ? ${key}
    then acc else acc // { ${key} = e.drv; }) { } analysisEntries;
  measurementPlan =
    assert lib.assertMsg (!(lib.any (e: (e.unclean or [ ]) != [ ] && !((e.drv or { }) ? withLock)) analysisEntries))
      "doc: ${name}: cannot remeasure static or untraced inputs";
    assert lib.assertMsg (lib.all (drv: lib.all (item: item.closures != { }) (lib.attrValues drv.remeasureSpec)) (lib.attrValues analysisDrvs))
      "doc: ${name}: an experiment recorded by hand has no measurement closure to rerun";
    pkgs.writeText "${name}-measurement-plan.json" (builtins.toJSON
    (lib.concatMap (drv: map (item: item // { analysis = builtins.unsafeDiscardStringContext (toString drv); })
      (lib.attrValues drv.remeasureSpec)) (lib.attrValues analysisDrvs)));

  marker = if remeasurement == null then null else
    pkgs.writeText "${name}-remeasurement.tex" "\\nixsci@remeasure{${remeasurement}}";
in
{ inherit analysisDrvs measurementPlan marker; }
