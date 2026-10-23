{ nixsci }:
{ inputs, root, system ? "x86_64-linux" }:
let
  pkgs = inputs.nixpkgs.legacyPackages.${system};
  inherit (pkgs) lib;
  doc = nixsci.lib.doc { inherit pkgs; };
  rootStr = toString root;

  declKinds = [ "project" "document" "template" ];
  skipped = n: n == "archive" || n == "node_modules" || lib.hasPrefix "." n || lib.hasPrefix "result" n;

  relPath = p: let s = toString p; in if s == rootStr then "" else lib.removePrefix (rootStr + "/") s;
  join = a: b: if a == "" then b else "${a}/${b}";

  scan = dir:
    let
      entries = builtins.readDir dir;
      here = lib.filter (k: entries ? "${k}.nix" && entries."${k}.nix" == "regular") declKinds;
      subdirs = lib.attrNames (lib.filterAttrs (n: t: t == "directory" && !skipped n) entries);
    in
    map (kind: { inherit kind dir; file = dir + "/${kind}.nix"; rel = relPath dir; }) here
    ++ lib.concatMap (n: scan (dir + "/${n}")) subdirs;
  found = scan root;

  label = d: join d.rel "${d.kind}.nix";
  partsOf = rel: lib.filter (c: c != "") (lib.splitString "/" rel);

  defaultName = d:
    let ps = partsOf d.rel;
    in if ps == [ ] then throw "workspace: ${label d}: name is required, the file is at the root of the workspace"
      else lib.concatStringsSep "-" (lib.takeEnd (if d.kind == "document" then 2 else 1) ps);

  schema = {
    project = [ "name" "style" "packages" "files" "data" "figures" "values" "bib" "passthru" ];
    template = [ "name" "files" "fonts" "class" "use" "style" "engine" "medium" "meta" "render" "figure" "packages" ];
    document = [ "name" "src" "entry" "template" "meta" "projects" "figures" "packages" "apps" "checks" ];
  };

  fileArgs = { inherit pkgs inputs doc; workspace = { inherit root system projects templates; }; };

  loadAttrs = d:
    let
      v = import d.file;
      attrs = if lib.isFunction v then v fileArgs else v;
      known = schema.${d.kind};
      unknown = lib.filter (k: !(lib.elem k known)) (lib.attrNames attrs);
    in
    if !lib.isAttrs attrs then throw "workspace: ${label d} must be an attrset, or a function returning one"
    else if unknown != [ ] then throw "workspace: ${label d}: unknown key `${lib.head unknown}` (the keys are: ${lib.concatStringsSep ", " known})"
    else attrs;

  named = kind: mk:
    let
      entries = map
        (d:
          let a = loadAttrs d; name = a.name or (defaultName d);
          in { inherit name; label = label d; value = mk d a name; })
        (lib.filter (d: d.kind == kind) found);
      names = map (e: e.name) entries;
      dupes = lib.unique (lib.filter (n: lib.length (lib.filter (m: m == n) names) > 1) names);
    in
    if dupes == [ ] then builtins.seq (lib.all (e: e.value.force or true) entries) (lib.listToAttrs (map (e: lib.nameValuePair e.name e.value) entries))
    else throw "workspace: ${kind} `${lib.head dupes}` is declared twice, in ${lib.concatStringsSep " and " (map (e: e.label) (lib.filter (e: e.name == lib.head dupes) entries))}";

  pathIn = dir: f: if lib.isString f then dir + "/${f}" else f;
  sibling = dir: f: if builtins.pathExists (dir + "/${f}") then dir + "/${f}" else null;

  packagesOf = p: if lib.isList p then ps: map (n: ps.${n}) p else p;

  callIf = v: if lib.isFunction v then v fileArgs else v;

  localFiguresIn = d: lib.listToAttrs (lib.concatLists (lib.mapAttrsToList
    (n: t:
      let m = builtins.match "(.+)\\.tex" n;
      in lib.optional (t == "regular" && m != null) (lib.nameValuePair (lib.head m) (d + "/${n}")))
    (builtins.readDir d)));

  mkProject = d: a: name:
    let
      dir = d.dir;
      assets = dir + "/assets";
      stem = n: let m = builtins.match "(.+)\\.[A-Za-z0-9]+" n; in if m == null then n else lib.head m;
      assetFiles = lib.optionalAttrs (builtins.pathExists assets) (lib.listToAttrs (lib.mapAttrsToList
        (n: t: lib.nameValuePair (if t == "regular" then stem n else n) (assets + "/${n}"))
        (builtins.readDir assets)));
      figuresDir = dir + "/figures";
      figuresIn = doc.figuresIn or localFiguresIn;
    in
    doc.project {
      inherit name;
      style = if a ? style then pathIn dir a.style else sibling dir "${name}.sty";
      files = a.files or assetFiles;
      figures =
        let f = a.figures or (if builtins.pathExists figuresDir then figuresDir else null);
        in if f == null then { } else if lib.isAttrs f then f else figuresIn (pathIn dir f);
      bib = if a ? bib then pathIn dir a.bib else sibling dir "references.bib";
      packages = packagesOf (a.packages or [ ]);
      data = a.data or { };
      values = a.values or { };
    } // { passthru = a.passthru or null; };

  mkTemplate = d: a: name:
    let
      dir = d.dir;
      given = k: f: lib.optionalAttrs (a ? ${k}) { ${k} = f a.${k}; };
    in
    doc.template ({ inherit name; } // removeAttrs a [ "name" "fonts" "style" "packages" ]
      // given "fonts" (lib.mapAttrs (_: f: doc.font f))
      // given "style" (pathIn dir)
      // given "packages" packagesOf);

  userTemplates = named "template" mkTemplate;
  templateLabels = lib.listToAttrs (map (d: lib.nameValuePair ((loadAttrs d).name or (defaultName d)) (label d))
    (lib.filter (d: d.kind == "template") found));
  candidate = source: local: name: value: { inherit name source local value; };
  candidates =
    lib.mapAttrsToList (n: v: candidate "the built-in templates" false n v) doc.templates
    ++ lib.concatLists (lib.mapAttrsToList
      (i: x: lib.mapAttrsToList (n: v: candidate "flake input ${i}" false n v) (x.lib.templates doc))
      (lib.filterAttrs (i: x: i != "self" && (x ? lib) && (x.lib ? templates)) inputs))
    ++ lib.mapAttrsToList (n: v: candidate templateLabels.${n} true n v) userTemplates;
  templates = lib.mapAttrs
    (name: cs:
      let
        locals = lib.filter (c: c.local) cs;
        sources = lib.concatStringsSep " and " (map (c: c.source) cs);
      in
      if locals != [ ] && lib.any (c: c.source == "the built-in templates") cs
      then throw "workspace: template `${name}` is both built-in and declared in ${(lib.head locals).source}"
      else if locals != [ ] then (lib.head locals).value
      else if lib.length cs == 1 then (lib.head cs).value
      else throw "workspace: template `${name}` is provided by ${sources}; rename one of them")
    (lib.groupBy (c: c.name) candidates);

  projects = named "project" mkProject;

  pick = known: kind: d: x:
    if lib.isString x then known.${x} or (throw "workspace: ${label d}: ${kind} `${x}` is not a ${kind} of this workspace (known: ${lib.concatStringsSep ", " (lib.attrNames known)})")
    else x;

  mkDocument = d: a: name:
    let
      dir = d.dir;
      srcArg = a.src or null;
      src =
        if srcArg == null then builtins.path {
          path = dir;
          name = "${name}-src";
          filter = p: _: baseNameOf (toString p) != "document.nix";
        }
        else pathIn dir srcArg;
      srcDir =
        if srcArg == null then d.rel
        else if lib.isString srcArg then join d.rel srcArg
        else if builtins.typeOf srcArg == "path" then relPath srcArg
        else null;
      tmpl = if a ? template then pick templates "template" d a.template else templates.plain;
      projs = map (pick projects "project" d) (a.projects or [ ]);
      out = doc.document ({
        inherit name src srcDir;
        entry = a.entry or "main.tex";
        template = tmpl;
        projects = projs;
        source = inputs.self or null;
      } // lib.optionalAttrs (a ? meta) { inherit (a) meta; }
        // lib.optionalAttrs (a ? figures) { inherit (a) figures; }
        // lib.optionalAttrs (a ? packages) { packages = packagesOf a.packages; });
    in
    {
      inherit out;
      force = builtins.seq tmpl (lib.all (x: x != null) projs);
      extraApps = callIf (a.apps or { });
      extraChecks = callIf (a.checks or { });
    };

  documents = named "document" mkDocument;
  per = f: lib.concatMapAttrs f documents;
in
{
  packages.${system} = per (n: x: let d = x.out; in {
    ${n} = d.pdf;
    "${n}-publish" = d.publish;
    "${n}-source" = d.source;
    "${n}-crate" = d.crate;
    "${n}-zip" = d.zip;
  });
  apps.${system} = per (n: x: { ${n} = x.out.apps.default; "${n}-open" = x.out.apps.open; } // x.extraApps);
  devShells.${system} = lib.mapAttrs (_: x: x.out.shell) documents;
  checks.${system} = per (n: x:
    lib.optionalAttrs (x.out ? zipCheck) { "${n}-zip-check" = x.out.zipCheck; } // x.extraChecks);
  lib = lib.mapAttrs (_: p: p.passthru) (lib.filterAttrs (_: p: p.passthru != null) projects);
}
