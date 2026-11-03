# Provenance: what a document's inputs can be traced to.
#
# An input is a file with the list of reasons a reader could not trace it to code and locked runs
# (`unclean`). Plain paths and derivations are clean; `static` marks a file copied by hand; `output`
# takes a file of a derivation and passes on the derivation's own `unclean` (a lab analysis that is
# not clean) and its `view` (the tables the analysis read).
{ lib }:
rec {
  # A file with its provenance. Plain paths and derivations are clean; a derivation that carries
  # `unclean` (a lab analysis) passes it on.
  entry = x:
    if lib.isAttrs x && x ? path && (x.type or null) != "derivation" then x
    else { path = x; unclean = if lib.isAttrs x then x.unclean or [ ] else [ ]; };

  # A file copied by hand: the build cannot say where its numbers came from.
  static = path: { inherit path; unclean = [ "static" ]; };

  # One file of a derivation's output, keeping the derivation's provenance and, for a lab analysis,
  # its view (the tables and run records the analysis read).
  output = drv: file: { path = "${drv}/${file}"; inherit drv file; unclean = drv.unclean or [ ]; view = drv.view or null; };

  # The lab analyses among a project's inputs, by key. Each one's `view` (set by lab.nix, carried
  # by `output`) is what the artifact keeps. Two different analyses under one key would land in one
  # directory, so that is an error.
  analysesOf = name: sets:
    let
      found = lib.concatLists (map (set: lib.mapAttrsToList (key: e: { inherit key; view = e.view; })
        (lib.filterAttrs (_: e: (e.view or null) != null) set)) sets);
      add = acc: a:
        if acc ? ${a.key} && toString acc.${a.key} != toString a.view
        then throw "doc: ${name}/${a.key} names two different analyses"
        else acc // { ${a.key} = a.view; };
    in lib.foldl' add { } found;

  # Every input of a list of projects that has a file (files, data, values), in one list.
  inputsOf = projects: lib.concatMap (p: lib.concatLists (map (set: lib.attrValues set)
    [ p.files p.data p.values ])) projects;

  # The reasons, over a list of projects, why the document is a draft.
  uncleanOf = projects: lib.concatMap (p: p.unclean) projects;

  # `e` with its analysis replaced by the one `replacements` names (keyed by derivation path), if any.
  replaceOutput = replacements: e:
    let e' = entry e; key = builtins.unsafeDiscardStringContext (toString (e'.drv or null));
    in if (e'.drv or null) != null && replacements ? ${key}
      then output replacements.${key} e'.file
      else e;
}
