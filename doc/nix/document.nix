# doc.document: the composition of the stages below. Each stage is a module with one job and explicit
# inputs, and this file is only the wiring between them:
#
#   meta        the document's meta, checked against the template's slots           (meta.nix)
#   figures     the variants of each project's figures, and their builds            (figure-build.nix)
#   remeasure   the lab analyses it reads, and the plan to measure them again       (remeasure.nix)
#   tree        the tree of toolkit, template, projects and figures, + the source   (tree.nix)
#   latex       the TeX environment, the command, the PDF                           (latex.nix)
#   publish     crate, zip, zipCheck, publish                                        (publish.nix)
#   dev         the dev shell and the apps                                           (dev.nix)
{ pkgs, lib, modules, templates, provenance }:
let
  inherit (modules) checkMeta figureBuild remeasure treeOf latexOf publishOf devOf;

  document =
    args@{ name
    , src
    , entry ? "main.tex"
    , template ? templates.plain
    , meta ? { }
    , projects ? [ ]
    , figures ? { }
    , packages ? (_: [ ])
    , source ? null
    , srcDir ? null
    , _remeasurement ? null
    }:
    let
      meta' = checkMeta { inherit name template meta; };
      unclean = provenance.uncleanOf projects;

      figs = figureBuild { inherit name template projects figures; };
      measure = remeasure { inherit name projects; remeasurement = _remeasurement; };
      trees = treeOf { inherit name template projects unclean src; meta = meta'; marker = measure.marker; figureBuild = figs; };
      latex = latexOf { inherit name template projects packages entry unclean; marked = unclean != [ ] || measure.marker != null; inherit (trees) sourceTree; };
      shipped = publishOf { inherit name template source projects unclean latex; inherit (trees) tree sourceTree fontFiles; };
      dev = devOf { inherit name template entry src srcDir latex; inherit (trees) tree; };
    in
    assert lib.assertMsg (figs.unknownFigures == [ ]) "doc: ${name}: ${lib.concatStringsSep "; " figs.unknownFigures}";
    {
      pdf = latex.pdf // {
        inherit (measure) measurementPlan;
        remeasureViews = { locks, level ? 0.95 }:
          lib.mapAttrs (key: drv: (drv.withLock locks.${key} level).view) measure.analysisDrvs;
        withMeasurements = { locks, level ? 0.95, remeasurement ? null }:
          let replacements = lib.mapAttrs (key: drv: drv.withLock locks.${key} level) measure.analysisDrvs;
              measuredProjects = map (p: p.withAnalyses replacements) projects;
          in (document (args // { projects = measuredProjects; _remeasurement = remeasurement; })).pdf;
      };
      inherit (shipped) publish refusals crate zip zipCheck;
      inherit (dev) apps;
      shell = dev.shell;
      inherit unclean;
      inherit (trees) generated;
      meta = meta';
      source = trees.sourceTree;
    };
in
document
