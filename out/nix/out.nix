# nixsci out: a document is a function of the words you wrote, the projects it reads, and a
# template. Four values, each a Nix function:
#
#   out = nixsci.lib.out { inherit pkgs; };
#
#   nerve = out.project {                                # what several documents share
#     name = "nerve";                                    # one namespace: nerve.sty, nerve/..., nerve.bib
#     style = ./nerve/nerve.sty;                         # \usepackage{nerve}
#     files.brain = ./nerve/assets/brain.pdf;            # \includegraphics{nerve/brain}
#     data.band = out.static ./nerve/data/band.tsv;      # copied by hand: untraced
#     figures.band = { src = ./nerve/figures/band.tex; data = [ "band" ]; };  # -> nerve/band.pdf
#     values.band = out.output analysis "band.json";     # \val{nerve/band.median}
#     bib = ./nerve/references.bib;                      # \bibliography{nerve}
#   };
#
#   talk = out.document {
#     name = "nerve-talk";
#     src = ./nerve/talk;  entry = "slides.tex";         # you keep main.tex; it names the class
#     template = out.templates.slides;  meta = { ... };  # metadata is a Nix value the template checks
#     projects = [ nerve ];
#     figures.nerve.band = "8.6cm";                      # this document's width for that figure
#     source = self;                                     # publish refuses uncommitted changes
#   };
#   talk.pdf  talk.publish  talk.source  talk.crate  talk.zip  talk.shell  talk.apps  talk.unclean
#
# A figure is compiled on its own (standalone class, the project's style, the template's fonts) at
# the width the document asks for. It reads its declared data at \datadir/<key>, and the project's
# files where a document sees them, <project>/<key>. Nothing from the document
# leaks into it and nothing in it needs patching to move between a paper and a slide.
#
# Provenance travels as `unclean`, a list of what a reader could not trace to code and locked
# runs: a file marked `static`, a lab analysis that is not clean. A document with any is a draft:
# every page says so, and `publish` refuses it, as it refuses an uncommitted tree.
{ pkgs, crateTool }:
let
  inherit (pkgs) lib;
  toolkit = ../files;

  # A file with its provenance. Plain paths and derivations are clean; a derivation that carries
  # `unclean` (a lab analysis) passes it on.
  entry = x:
    if lib.isAttrs x && x ? path && (x.type or null) != "derivation" then x
    else { path = x; unclean = if lib.isAttrs x then x.unclean or [ ] else [ ]; };

  extension = p: let m = builtins.match ".*/[^/]*\\.([A-Za-z0-9]+)" (toString p); in if m == null then "" else ".${lib.head m}";

  texEnv = packages: pkgs.texliveSmall.withPackages packages;

  engineFlag = engine:
    { pdflatex = "-pdf"; lualatex = "-lualatex"; xelatex = "-xelatex"; }.${engine}
      or (throw "out: engine must be pdflatex, lualatex or xelatex, not ${engine}");

  # A font-aware engine builds a font cache on first use and needs a writable HOME for it
  # (luaotfload ignores TEXMFVAR and TEXMFCACHE; measured).
  engineHome = engine: lib.optionalString (engine != "pdflatex") ''export HOME="$TMPDIR"'';

  # The lab analyses among a project's inputs, by key. Each one's `view` (set by lab.nix, carried
  # by `output`) is what the artifact keeps. Two different analyses under one key would land in one
  # directory, so that is an error.
  analysesOf = name: sets:
    let
      found = lib.concatLists (map (set: lib.mapAttrsToList (key: e: { inherit key; view = e.view; })
        (lib.filterAttrs (_: e: (e.view or null) != null) set)) sets);
      add = acc: a:
        if acc ? ${a.key} && toString acc.${a.key} != toString a.view
        then throw "out: ${name}/${a.key} names two different analyses"
        else acc // { ${a.key} = a.view; };
    in lib.foldl' add { } found;
in
rec {
  # A file copied by hand: the build cannot say where its numbers came from.
  static = path: { inherit path; unclean = [ "static" ]; };

  # One file of a derivation's output, keeping the derivation's provenance and, for a lab analysis,
  # its view (the tables and run records the analysis read).
  output = drv: file: { path = "${drv}/${file}"; unclean = drv.unclean or [ ]; view = drv.view or null; };

  project =
    { name
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
            data = map (d: if data' ? ${d} then d else throw "out: figure ${name}/${key} reads data ${d}, which ${name} does not declare") (f'.data or [ ]);
            options = f'.options or "";
          })
        figures;
      tagged = kind: set: lib.concatLists (lib.mapAttrsToList (k: e: map (why: "${name}/${kind}/${k}: ${why}") e.unclean) set);
      files' = lib.mapAttrs (_: entry) files;
      values' = lib.mapAttrs (_: entry) values;
    in
    {
      inherit name style packages bib;
      files = files';
      data = data';
      figures = figures';
      values = values';
      unclean = tagged "files" files' ++ tagged "data" data' ++ tagged "values" values';
      analyses = analysesOf name [ files' data' values' ];
    };

  # files: a directory whose entries land at the root of the tree, or a list of files.
  # meta: slot -> "required" | "optional", or null for any slots (a document that names its own
  # class keeps its metadata for other documents to read). render: the meta -> TeX the class reads.
  # figure: what a figure compiled for this template gets (fonts, default width).
  template =
    { name
    , files ? null
    , packages ? (_: [ ])
    , engine ? "pdflatex"
    , meta ? { }
    , render ? (_: "")
    , figure ? { }
    }:
    {
      inherit name files packages engine meta render;
      # size: the base font size, as the document's class sets it; \small in a figure means what it
      # means on the page only if both start from the same size.
      figure = { width = "12cm"; size = "10pt"; preamble = ""; packages = _: [ ]; } // figure;
    };

  templates = {
    # The document's main.tex names its own class; the template adds only the toolkit.
    plain = template { name = "plain"; packages = ps: [ ps.latexmk ]; files = toolkit; meta = null; };
    paper = template { name = "paper"; files = toolkit; packages = ps: with ps; [ latexmk biblatex biber cleveref siunitx pgfplots enumitem ]; };
    # natbib ships bibentry.sty in nixpkgs, which the slides style's \citehere reads.
    slides = template { name = "slides"; files = toolkit; packages = ps: with ps; [ latexmk beamer pgfplots siunitx natbib ]; };
    poster = template { name = "poster"; files = toolkit; packages = ps: with ps; [ latexmk tikzposter a0poster xstring pgfplots siunitx ]; };
  };

  document =
    { name
    , src
    , entry ? "main.tex"
    , template ? templates.plain
    , meta ? { }
    , projects ? [ ]
    , figures ? { }
    , packages ? (_: [ ])
    , source ? null
    , srcDir ? null
    }:
    let
      t = template;
      flag = engineFlag t.engine;

      required = lib.attrNames (lib.filterAttrs (_: v: v == "required") t.meta);
      metaErrors =
        map (k: "${k} is required") (lib.filter (k: !(meta ? ${k})) required)
        ++ map (k: "${k} is not a slot of ${t.name} (slots: ${lib.concatStringsSep ", " (lib.attrNames t.meta)})")
          (lib.filter (k: !(t.meta ? ${k})) (lib.attrNames meta));
      meta' = if t.meta == null || metaErrors == [ ] then meta else throw "out: ${name}: meta: ${lib.concatStringsSep "; " metaErrors}";

      byName = lib.listToAttrs (map (p: lib.nameValuePair p.name p) projects);
      unknownFigures = lib.concatLists (lib.mapAttrsToList
        (p: fs: if byName ? ${p} then [ ] else [ "project ${p} is not among this document's projects" ]) figures);

      # Every figure of every project, at the template's width unless this document names another,
      # plus the variants it adds with `from` (and TeX `options` set before the figure is read).
      variants = p:
        let
          asked = figures.${p.name} or { };
          spec = key: v:
            let v' = if lib.isString v then { width = v; } else v; from = v'.from or key;
            in
            if p.figures ? ${from}
            then { inherit key; fig = p.figures.${from} // lib.optionalAttrs (v' ? options) { inherit (v') options; }; width = v'.width or t.figure.width; }
            else throw "out: ${name}: figure ${p.name}/${from} is not a figure of ${p.name}";
        in
        lib.mapAttrsToList (key: _: spec key (asked.${key} or t.figure.width)) p.figures
        ++ lib.mapAttrsToList spec (lib.filterAttrs (k: _: !(p.figures ? ${k})) asked);

      figureEnv = texEnv (ps: [ ps.latexmk ps.standalone ] ++ t.packages ps ++ t.figure.packages ps ++ lib.concatMap (p: p.packages ps) projects);
      compile = p: v: pkgs.runCommand "${p.name}-${v.key}-${t.name}"
        { nativeBuildInputs = [ figureEnv ]; FORCE_SOURCE_DATE = "1"; } ''
        ${engineHome t.engine}
        mkdir -p data ${p.name}
        ${lib.concatMapStrings (d: "cp ${p.data.${d}.path} data/${d}${extension p.data.${d}.path}\n") v.fig.data}
        ${lib.concatStrings (lib.mapAttrsToList (k: e: "cp -RL --no-preserve=mode ${e.path} ${p.name}/${k}${extension e.path}\n") p.files)}
        ${lib.optionalString (p.style != null) "cp ${p.style} ${p.name}.sty"}
        cp ${v.fig.src} figure.tex
        cat > wrapper.tex <<'EOF'
        \documentclass[border=0pt,${t.figure.size}]{standalone}
        ${t.figure.preamble}
        ${lib.optionalString (p.style != null) "\\usepackage{${p.name}}"}
        \def\datadir{data}\def\figwidth{${v.width}}
        ${v.fig.options}
        \begin{document}\input{figure}\end{document}
        EOF
        latexmk ${flag} -interaction=nonstopmode -halt-on-error wrapper.tex >/dev/null || { cat wrapper.log; exit 1; }
        mkdir $out
        cp wrapper.pdf $out/${v.key}.pdf
      '';

      unclean = lib.concatMap (p: p.unclean) projects;

      # metadata, values and the draft mark, read by nixsci.sty
      generated = pkgs.runCommand "${name}-generated" { nativeBuildInputs = [ pkgs.jq ]; } ''
        {
          echo '% Generated by nixsci out from the document flake. Edit the flake, not this file.'
          cat ${pkgs.writeText "${name}-meta.tex" (t.render meta')}
          ${lib.concatMapStrings (p: lib.concatStrings (lib.mapAttrsToList (k: e: ''
            jq -r --arg p ${lib.escapeShellArg "${p.name}/${k}"} \
              'if (type == "object" or type == "array") then (paths(scalars) as $k | "\\nixsci@defval{\($p).\($k | map(tostring) | join("."))}{\(getpath($k))}") else "\\nixsci@defval{\($p)}{\(.)}" end' \
              ${e.path}
          '') p.values)) projects}
          ${lib.optionalString (unclean != [ ]) "echo '\\nixsci@draft{${toString (lib.length unclean)}}'"}
        } > $out
      '';

      # Everything that is not the document's own words, laid out as LaTeX will name it, and a TSV
      # of where each file came from (the crate's per-file isBasedOn).
      tree = pkgs.runCommand "${name}-tree" { } ''
        mkdir -p $out/tree
        : > $out/origins.tsv
        put() { # put <tree path> <store path>
          [ ! -e "$out/tree/$1" ] || { echo "out: $1 is provided twice" >&2; exit 1; }
          mkdir -p "$out/tree/$(dirname "$1")"
          cp -RL --no-preserve=mode "$2" "$out/tree/$1"
          printf '%s\t%s\n' "$1" "$2" >> $out/origins.tsv
        }
        ${if t.files == null then ""
          else if lib.isList t.files then lib.concatMapStrings (f: "put ${baseNameOf (toString f)} ${f}\n") t.files
          else ''for f in ${t.files}/*; do put "$(basename "$f")" "$f"; done''}
        [ -e $out/tree/nixsci.sty ] || put nixsci.sty ${toolkit}/nixsci.sty
        put nixsci-generated.tex ${generated}
        ${lib.concatMapStrings (p:
          lib.optionalString (p.style != null) "put ${p.name}.sty ${p.style}\n"
          + lib.optionalString (p.bib != null) "put ${p.name}.bib ${p.bib}\n"
          + lib.concatStrings (lib.mapAttrsToList (k: e: "put ${lib.escapeShellArg "${p.name}/${k}${extension e.path}"} ${e.path}\n") p.files)
          + lib.concatMapStrings (v: "put ${lib.escapeShellArg "${p.name}/${v.key}.pdf"} ${compile p v}/${v.key}.pdf\n") (variants p))
          projects}
      '';

      texPkgs = ps: t.packages ps ++ packages ps ++ lib.concatMap (p: p.packages ps) projects;
      texEnv' = texEnv texPkgs;
      # A TeX Live package's name: an entry of the texlive set carries it in its `pkgs` list.
      texName = p: p.pname or (lib.head p.pkgs).pname;
      texNames = lib.sort (a: b: a < b) (lib.unique (map texName (texPkgs pkgs.texlive)));

      sourceTree = pkgs.runCommand "${name}-source" { } ''
        mkdir $out
        cp -RL --no-preserve=mode ${tree}/tree/. $out/
        while read -r f; do
          [ ! -e "$out/$f" ] || { echo "out: $f is in both the document and its projects or template" >&2; exit 1; }
        done < <(cd ${src} && find . -type f)
        cp -RL --no-preserve=mode ${src}/. $out/
      '';

      command = [ "latexmk" flag "-interaction=nonstopmode" "-file-line-error" "-halt-on-error" entry ];
      stem = lib.removeSuffix ".tex" entry;

      # FORCE_SOURCE_DATE: the PDF carries the sandbox's fixed date, not the build time. The receipt
      # is written after latexmk returned, so it records a compile that happened.
      pdf = pkgs.runCommand "${name}-pdf" { nativeBuildInputs = [ texEnv' pkgs.jq ]; FORCE_SOURCE_DATE = "1"; } ''
        ${engineHome t.engine}
        cp -RL --no-preserve=mode ${sourceTree}/. .
        ${lib.escapeShellArgs command}
        mkdir -p $out
        cp ${stem}.pdf $out/${name}.pdf
        jq -n --argjson command ${lib.escapeShellArg (builtins.toJSON command)} \
          --argjson unclean ${lib.escapeShellArg (builtins.toJSON unclean)} \
          --arg tex ${texEnv'} --arg version "$(latexmk -v | head -n 2 | tail -n 1)" \
          '{command: $command, exitStatus: 0, texEnvironment: $tex, latexmk: $version, unclean: $unclean}' > $out/receipt.json
      '';

      crate = pkgs.runCommand "${name}-ro-crate" { nativeBuildInputs = [ crateTool ]; } ''
        python ${../crate.py} --name ${lib.escapeShellArg name} --source ${sourceTree} \
          --pdf ${pdf}/${name}.pdf --receipt ${pdf}/receipt.json --includes ${pkgs.writeText "none.json" "{}"} \
          --origins ${tree}/origins.tsv --out $out
      '';

      dirty = source != null && !(source ? rev);
      refusals = unclean
        ++ lib.optional (source == null) "the document does not name its flake (source = self), so its tree cannot be checked"
        ++ lib.optional dirty "the flake has uncommitted changes";
      # Refused at build time, not evaluation: the flake still evaluates while you write.
      publish =
        if refusals == [ ] then publishDir
        else pkgs.runCommand "${name}-publish-refused" { } ''
          echo ${lib.escapeShellArg "out: ${name} cannot be published:\n  ${lib.concatStringsSep "\n  " refusals}"} >&2
          exit 1
        '';

      readme = pkgs.writeText "${name}-README.txt" ''
        ${name}-latex.zip: the LaTeX source of ${name}, with its figures and style files.
        Unzip it into an empty directory, then run this in that directory:

            latexmk ${flag} ${entry}

        That writes ${stem}.pdf. It needs these TeX Live packages (texliveSmall; the packages they
        depend on come with them): ${lib.concatStringsSep ", " texNames}.
        ${lib.optionalString (t.engine != "pdflatex") "${t.engine} writes a font cache: set HOME to a writable directory first."}
      '';

      # The LaTeX project: the source tree and README.txt. Sorted names and fixed times, so two
      # builds give the same bytes.
      zip = pkgs.runCommand "${name}-zip" { nativeBuildInputs = [ pkgs.zip ]; } ''
        mkdir -p $out work
        cp -RL --no-preserve=mode ${sourceTree}/. work/
        [ ! -e work/README.txt ] || { echo "out: ${name} has a README.txt at its root; the LaTeX zip adds one" >&2; exit 1; }
        cp ${readme} work/README.txt
        cd work
        find . -exec touch -h -d @0 {} +
        find . -type f -printf '%P\n' | LC_ALL=C sort | zip -X -D -q $out/${name}.zip -@
      '';

      analysisViews = lib.concatMap (p: lib.mapAttrsToList (key: view: { project = p.name; inherit key view; }) p.analyses) projects;

      # publish: the document, its LaTeX project, and the reproduction artifact, with SHA256SUMS.
      publishDir = pkgs.runCommand "${name}-publish" { nativeBuildInputs = [ pkgs.zip ]; } ''
        mkdir -p $out art
        cp ${pdf}/${name}.pdf $out/${name}.pdf
        cp ${zip}/${name}.zip $out/${name}-latex.zip
        cp -RL --no-preserve=mode ${crate}/. art/
        cat > art/ARTIFACT-README.txt <<'EOF'
        This archive contains the RO-Crate files and the analysis views listed under data/.
        These views are not full raw experiment run directories.
        EOF
        ${lib.concatMapStrings (a: ''
          mkdir -p ${lib.escapeShellArg "art/data/${a.project}/${a.key}/view"}
          cp -RL --no-preserve=mode ${a.view}/. ${lib.escapeShellArg "art/data/${a.project}/${a.key}/view"}/
        '') analysisViews}
        find art -exec touch -h -d @0 {} +
        (cd art && find . -type f -printf '%P\n' | LC_ALL=C sort | zip -X -D -q $out/${name}-artifact.zip -@)
        cd $out
        files=$(find . -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort)
        sha256sum $files > SHA256SUMS
      '';

      # The dev loop edits the checkout: the tree (template, toolkit, projects, compiled figures) is
      # symlinked under .nixsci/out/<name> and put on the TeX search paths. A figure source edit
      # needs `nix run` again, because the figure is a build.
      sourceDir =
        if srcDir != null then srcDir
        else if builtins.typeOf src == "path" then baseNameOf (toString src)
        else null;
      env = ''
        root=$PWD
        mkdir -p "$root/.nixsci/out"
        ln -sfn ${tree}/tree "$root/.nixsci/out/${name}"
        # For a document that must name a real directory (fontspec's Path=): the build sets nothing
        # and runs in the tree, so `os.getenv("NIXSCI_TREE") or "."` names it in both.
        export NIXSCI_TREE="$root/.nixsci/out/${name}"
        export TEXINPUTS="$root/.nixsci/out/${name}//:''${TEXINPUTS:-}"
        export BIBINPUTS="$root/.nixsci/out/${name}:''${BIBINPUTS:-}"
        export BSTINPUTS="$root/.nixsci/out/${name}:''${BSTINPUTS:-}"
      '';
      shell = pkgs.mkShell {
        packages = [ texEnv' pkgs.xdg-utils ];
        shellHook = env + lib.optionalString (sourceDir != null) ''echo "out: cd ${sourceDir}; latexmk -pvc ${flag} ${entry}"'';
      };
      devApp = pkgs.writeShellApplication {
        name = "${name}-dev";
        runtimeInputs = [ texEnv' pkgs.xdg-utils ];
        text = env + (if sourceDir == null then ''
          echo "out: ${name} needs srcDir for the dev loop: src is not a plain path" >&2
          exit 1
        '' else ''
          cd "$root/${sourceDir}"
          # -g: a stale latexmk state file can record an error and then refuse to recompile.
          latexmk -g ${flag} -interaction=nonstopmode -file-line-error -halt-on-error ${entry}
          xdg-open "${stem}.pdf" &
          exec latexmk -pvc ${flag} -interaction=nonstopmode -file-line-error -halt-on-error ${entry}
        '');
      };
      openApp = pkgs.writeShellApplication {
        name = "${name}-open";
        runtimeInputs = [ pkgs.xdg-utils ];
        text = "exec xdg-open ${pdf}/${name}.pdf";
      };
    in
    assert lib.assertMsg (unknownFigures == [ ]) "out: ${name}: ${lib.concatStringsSep "; " unknownFigures}";
    {
      inherit pdf publish refusals crate zip shell unclean;
      meta = meta';
      source = sourceTree;
      apps = {
        default = { type = "app"; program = "${devApp}/bin/${name}-dev"; };
        open = { type = "app"; program = "${openApp}/bin/${name}-open"; };
      };
    };

  inherit toolkit;
}
