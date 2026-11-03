# doc.project: what several documents share (a style, files, data, figures, values, a bibliography).
# See doc.nix for the arguments; this is the function and the provenance it records.
{ lib, provenance, pkgNames }:
let
  inherit (provenance) entry analysesOf replaceOutput;

  project =
    args@{ name
    , style ? null
    , packages ? (_: [ ])
    , files ? { }
    , data ? { }
    , figures ? { }
    , values ? { }
    , bib ? null
    }:
    let
      data' = lib.mapAttrs (_: entry) data;
      figures' = lib.mapAttrs
        (key: f:
          let f' = if lib.isAttrs f && f ? src then f else { src = f; };
          in {
            src = f'.src;
            data = map (d: if data' ? ${d} then d else throw "doc: figure ${name}/${key} reads data ${d}, which ${name} does not declare") (f'.data or [ ]);
            # DEPRECATED: `options` (TeX in a string) is replaced by `params`; deleted once the papers migrate.
            options = f'.options or "";
          })
        figures;
      tagged = kind: set: lib.concatLists (lib.mapAttrsToList (k: e: map (why: "${name}/${kind}/${k}: ${why}") e.unclean) set);
      files' = lib.mapAttrs (_: entry) files;
      values' = lib.mapAttrs (_: entry) values;
    in
    {
      inherit name style bib;
      packages = pkgNames packages;
      files = files';
      data = data';
      figures = figures';
      values = values';
      unclean = tagged "files" files' ++ tagged "data" data' ++ tagged "values" values';
      analyses = analysesOf name [ files' data' values' ];
      withAnalyses = replacements:
        project ((args // {
          files = lib.mapAttrs (_: e: replaceOutput replacements e) (args.files or { });
          data = lib.mapAttrs (_: e: replaceOutput replacements e) (args.data or { });
          values = lib.mapAttrs (_: e: replaceOutput replacements e) (args.values or { });
        }));
    };
in
project
