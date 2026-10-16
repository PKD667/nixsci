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
# the width the document asks for, and included at its natural size. Nothing from the document
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
in
rec {
  # A file copied by hand: the build cannot say where its numbers came from.
  static = path: { inherit path; unclean = [ "static" ]; };

  # One file of a derivation's output, keeping the derivation's provenance.
  output = drv: file: { path = "${drv}/${file}"; unclean = drv.unclean or [ ]; };

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
    };

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
      figure = { width = "12cm"; preamble = ""; packages = _: [ ]; } // figure;
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
      # plus the variants it adds with `from`.
      variants = p:
        let
          asked = figures.${p.name} or { };
          spec = key: v:
            let v' = if lib.isString v then { width = v; } else v; from = v'.from or key;
            in
            if p.figures ? ${from} then { inherit key; fig = p.figures.${from}; width = v'.width or t.figure.width; }
            else throw "out: ${name}: figure ${p.name}/${from} is not a figure of ${p.name}";
        in
        lib.mapAttrsToList (key: _: spec key (asked.${key} or t.figure.width)) p.figures
        ++ lib.mapAttrsToList spec (lib.filterAttrs (k: _: !(p.figures ? ${k})) asked);

      figureEnv = texEnv (ps: [ ps.latexmk ps.standalone ] ++ t.packages ps ++ t.figure.packages ps ++ lib.concatMap (p: p.packages ps) projects);
      compile = p: v: pkgs.runCommand "${p.name}-${v.key}-${t.name}"
        { nativeBuildInputs = [ figureEnv ]; FORCE_SOURCE_DATE = "1"; } ''
        ${engineHome t.engine}
        mkdir -p data
        ${lib.concatMapStrings (d: "cp ${p.data.${d}.path} data/${d}${extension p.data.${d}.path}\n") v.fig.data}
        ${lib.optionalString (p.style != null) "cp ${p.style} ${p.name}.sty"}
        cp ${v.fig.src} figure.tex
        cat > wrapper.tex <<'EOF'
        \documentclass[border=0pt]{standalone}
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
        ${lib.optionalString (t.files != null) ''
          for f in ${t.files}/*; do put "$(basename "$f")" "$f"; done
        ''}
        [ -e $out/tree/nixsci.sty ] || put nixsci.sty ${toolkit}/nixsci.sty
        put nixsci-generated.tex ${generated}
        ${lib.concatMapStrings (p:
          lib.optionalString (p.style != null) "put ${p.name}.sty ${p.style}\n"
          + lib.optionalString (p.bib != null) "put ${p.name}.bib ${p.bib}\n"
          + lib.concatStrings (lib.mapAttrsToList (k: e: "put ${lib.escapeShellArg "${p.name}/${k}${extension e.path}"} ${e.path}\n") p.files)
          + lib.concatMapStrings (v: "put ${lib.escapeShellArg "${p.name}/${v.key}.pdf"} ${compile p v}/${v.key}.pdf\n") (variants p))
          projects}
      '';

      texEnv' = texEnv (ps: t.packages ps ++ packages ps ++ lib.concatMap (p: p.packages ps) projects);

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
        if refusals == [ ] then crate
        else pkgs.runCommand "${name}-publish-refused" { } ''
          echo ${lib.escapeShellArg "out: ${name} cannot be published:\n  ${lib.concatStringsSep "\n  " refusals}"} >&2
          exit 1
        '';

      zip = pkgs.runCommand "${name}-zip" { nativeBuildInputs = [ pkgs.zip ]; } ''
        mkdir -p $out
        cd ${sourceTree}
        zip -r -X $out/${name}.zip .
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
