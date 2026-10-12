# A document (paper, slides, poster) as a pure function of its source tree, the files that analyses
# produced, and a TeX environment.
#
#   out = nixsci.lib.out { inherit pkgs; };
#   doc = out.document {
#     name = "nerve";
#     template = out.templates.paper;
#     src = ./tex;                                      # hand-written: main.tex, references.bib, diagrams
#     include."gen/cost.pdf" = "${analysis}/cost.pdf";  # computed: figures and `\newcommand` files
#     assets = { nerve.cost = "${analysis}/cost.pdf"; };  # the library: a key is an attrpath
#     use = [ "nerve.cost" ];
#   };
#   doc.source  doc.pdf  doc.crate  doc.zip  doc.shell  doc.apps
#
# `source` is ordinary LaTeX with symlinks dereferenced: send it to Overleaf or arXiv unchanged.
# Four trees are merged into it, template files, `src`, the used assets, and `include`; a path in
# two of them is an error, because a silent overwrite would make the PDF depend on merge order.
#
# The library compiles down to that same merge. A key is an attrpath into `assets` and the tree
# path is that attrpath, so the Nix path is the LaTeX name and neither side learns a syntax. A leaf
# is a file or a directory, in the repository or a derivation output; `packages` on a leaf joins
# the TeX environment, for a shared `\newcommand` set that cannot be separated from what it calls.
{ pkgs, crateTool }:
let
  inherit (pkgs) lib;

  # The toolkit file every document gets: `\usepackage{nixsci}` then `\nixscistyle{<medium>}`.
  toolkitFiles = ../files;

  templates = {
    paper = { packages = ps: with ps; [ latexmk biblatex biber cleveref siunitx pgfplots enumitem ]; files = toolkitFiles; };
    # natbib ships bibentry.sty in nixpkgs, which the slides style's \citehere reads.
    slides = { packages = ps: with ps; [ latexmk beamer pgfplots siunitx natbib ]; files = toolkitFiles; };
    poster = { packages = ps: with ps; [ latexmk tikzposter a0poster xstring pgfplots siunitx ]; files = toolkitFiles; };
  };

  # The used keys, laid out at their key paths in one tree, plus a TSV of the store path each
  # laid-out file came from. The TSV is the crate's per-file `isBasedOn`.
  assemble =
    { name, assets, use }:
    let
      keysOf = prefix: set:
        lib.concatLists (lib.mapAttrsToList
          (k: v:
            if lib.isAttrs v && (v.type or null) != "derivation" && !(v ? path)
            then keysOf (prefix ++ [ k ]) v
            else [ (lib.concatStringsSep "." (prefix ++ [ k ])) ])
          set);
      known = keysOf [ ] assets;
      entryOf = key:
        let v = lib.attrByPath (lib.splitString "." key) null assets;
        in if v == null
        then throw "out: asset ${key} is not in the library; known keys: ${lib.concatStringsSep ", " known}"
        else if lib.isAttrs v && (v.type or null) != "derivation" && v ? path
        then { path = v.path; packages = v.packages or (_: [ ]); }
        else { path = v; packages = (_: [ ]); };
      entries = map (key: (entryOf key) // { inherit key; }) use;
      # `"${e.path}"`, not `toString e.path`: interpolation carries the store-path context the
      # derivation needs as an input, and toString on a path inside a flake drops it.
      put = e: "put ${lib.escapeShellArg e.key} ${lib.escapeShellArg "${e.path}"}";
    in
    {
      packages = ps: lib.concatMap (e: e.packages ps) entries;
      tree = pkgs.runCommand "${name}-assets" { } ''
        mkdir -p $out/tree
        : > $out/origins.tsv
        put() { # a file keeps the key's name and its own extension; a directory keeps the key
          key=$(printf '%s' "$1" | tr . /); src=$2
          dir=''${key%/*}; [ "$dir" = "$key" ] && dir=
          base=''${key##*/}; dest="''${dir:+$dir/}$base"
          mkdir -p "$out/tree/''${dir:-.}"
          if [ -f "$src" ]; then
            ext=''${src##*.}
            [ "$ext" != "$src" ] || ext=
            dst="$dest''${ext:+.$ext}"
            cp "$src" "$out/tree/$dst"
            printf '%s\t%s\n' "$dst" "$src" >> $out/origins.tsv
          else
            cp -r "$src" "$out/tree/$dest"
            (cd "$src" && find . -type f) | while read -r f; do
              printf '%s\t%s\n' "$dest/''${f#./}" "$src/''${f#./}" >> $out/origins.tsv
            done
          fi
        }
        ${lib.concatMapStringsSep "\n" put entries}
      '';
    };

  document =
    { name
    , template
    , src
    , entry ? "main.tex"
    , include ? { }
    , assets ? { }
    , use ? [ ]
    , srcDir ? null
    , engine ? "pdflatex"
    , packages ? (_: [ ])
    }:
    let
      library = assemble { inherit name assets use; };
      texEnv = pkgs.texliveSmall.withPackages (ps: template.packages ps ++ packages ps ++ library.packages ps);

      # The TeX engine, as latexmk's selector for it. pdfLaTeX by default; a document that loads a
      # font pdfLaTeX cannot read (fontspec, an OTF) passes engine = "lualatex" or "xelatex" from
      # its own flake, which is where that choice belongs. The receipt records the command, so the
      # crate says which engine produced the PDF.
      engineFlag =
        { pdflatex = "-pdf"; lualatex = "-lualatex"; xelatex = "-xelatex"; }
        .${engine} or (throw "out: engine must be pdflatex, lualatex or xelatex, not ${engine}");

      # The dev loop edits a live checkout, so it needs the source directory by name; `src` alone
      # may be a filtered store copy. A plain path names itself; anything else passes `srcDir`.
      sourceDir =
        if srcDir != null then srcDir
        else if builtins.typeOf src == "path" then baseNameOf (toString src)
        else null;

      # The template's files (a house style) are merged into the build tree. The dev loop edits the
      # checkout, so they go on TEXINPUTS instead of into a tree that must stay immutable.
      templateInputs = lib.optionalString (template.files != null) "${template.files}:";

      putTree = from: "put ${lib.escapeShellArg "${from}"}";
      putFile = dest: from: ''put_file ${lib.escapeShellArg dest} ${lib.escapeShellArg "${from}"}'';

      source = pkgs.runCommand "${name}-source" { } ''
        mkdir $out
        put() { # tree: every file must be new
          while read -r f; do
            [ ! -e "$out/$f" ] || { echo "out: $f is in two of template, src, assets, include" >&2; exit 1; }
          done < <(cd "$1" && find . -type f)
          cp -RL --no-preserve=mode "$1"/. $out/
        }
        put_file() {
          [ ! -e "$out/$1" ] || { echo "out: $1 is in two of template, src, assets, include" >&2; exit 1; }
          mkdir -p "$out/$(dirname "$1")"
          cp -RL --no-preserve=mode "$2" "$out/$1"
        }
        ${lib.optionalString (template.files != null) (putTree template.files)}
        ${putTree src}
        ${lib.optionalString (use != [ ]) (putTree "${library.tree}/tree")}
        ${lib.concatStringsSep "\n" (lib.mapAttrsToList putFile include)}
      '';

      command = [ "latexmk" engineFlag "-interaction=nonstopmode" "-file-line-error" "-halt-on-error" entry ];
      receipt = pkgs.writeText "${name}-render-receipt.json" (builtins.toJSON {
        inherit command entry;
        exitStatus = 0;
        texEnvironment = toString texEnv;
      });
      includes = pkgs.writeText "${name}-includes.json" (builtins.toJSON (lib.mapAttrs (_: f: "${f}") include));

      # FORCE_SOURCE_DATE: the PDF carries the sandbox's fixed date, not the build time.
      # A luatex or xetex engine builds a font cache on first use and refuses to start without a
      # writable one. The sandbox's HOME is not writable, and luaotfload ignores TEXMFVAR and
      # TEXMFCACHE (measured: only HOME does it), so the engine gets HOME under $TMPDIR.
      # pdfLaTeX needs none of this.
      pdf = pkgs.runCommand "${name}-pdf" { nativeBuildInputs = [ texEnv ]; FORCE_SOURCE_DATE = "1"; } ''
        ${lib.optionalString (engine != "pdflatex") ''
          export HOME="$TMPDIR"
          mkdir -p "$HOME"
        ''}
        cp -RL --no-preserve=mode ${source}/. .
        ${lib.escapeShellArgs command}
        mkdir -p $out
        cp ${lib.removeSuffix ".tex" entry}.pdf $out/${name}.pdf
        cp ${receipt} $out/receipt.json
        latexmk -v > $out/compiler-version.txt
      '';

      crate = pkgs.runCommand "${name}-ro-crate" { nativeBuildInputs = [ crateTool ]; } ''
        python ${../crate.py} --name ${lib.escapeShellArg name} --source ${source} \
          --pdf ${pdf}/${name}.pdf --receipt ${pdf}/receipt.json --includes ${includes} \
          --origins ${library.tree}/origins.tsv --out $out
      '';

      # Overleaf takes a zip. The source tree already holds the used assets, so this is that tree.
      zip = pkgs.runCommand "${name}-zip" { nativeBuildInputs = [ pkgs.zip ]; } ''
        mkdir -p $out
        cd ${source}
        zip -r -X $out/${name}.zip .
      '';

      # Editing. `nix run` is the dev loop; `nix develop` is the same TeX with the library on
      # TEXINPUTS, for `latexmk -pvc` by hand. Both work on the checkout, so the library is
      # symlinked in under a gitignored directory rather than copied from the store tree.
      shell = pkgs.mkShell {
        packages = [ texEnv pkgs.xdg-utils ];
        shellHook = ''
          root=$PWD
          mkdir -p "$root/.nixsci/out"
          ln -sfn ${library.tree}/tree "$root/.nixsci/out/assets"
          export TEXINPUTS="$root/.nixsci/out/assets:${templateInputs}''${TEXINPUTS:-}"
          # bibtex does not read TEXINPUTS: a .bib or .bst that is a library key is found through
          # the same assets symlink and these two paths.
          export BIBINPUTS="$root/.nixsci/out/assets:''${BIBINPUTS:-}"
          export BSTINPUTS="$root/.nixsci/out/assets:''${BSTINPUTS:-}"
          ${lib.optionalString (sourceDir != null) ''echo "out: cd ${sourceDir}; latexmk -pvc ${engineFlag} ${entry}"''}
        '';
      };

      devApp = pkgs.writeShellApplication {
        name = "${name}-dev";
        runtimeInputs = [ texEnv pkgs.xdg-utils ];
        text = ''
          root=$PWD
          mkdir -p "$root/.nixsci/out"
          ln -sfn ${library.tree}/tree "$root/.nixsci/out/assets"
          export TEXINPUTS="$root/.nixsci/out/assets:${templateInputs}''${TEXINPUTS:-}"
          export BIBINPUTS="$root/.nixsci/out/assets:''${BIBINPUTS:-}"
          export BSTINPUTS="$root/.nixsci/out/assets:''${BSTINPUTS:-}"
          ${if sourceDir == null then ''
            echo "out: ${name} needs srcDir for the dev loop: src is not a plain path" >&2
            exit 1
          '' else ''
            cd "$root/${sourceDir}"
            # `-g`: a stale latexmk state file can record an error and then, with no source change,
            # call the target up to date and refuse to recompile. Force the first pass.
            latexmk -g ${engineFlag} -interaction=nonstopmode -file-line-error -halt-on-error ${entry}
            xdg-open "${lib.removeSuffix ".tex" entry}.pdf" &
            exec latexmk -pvc ${engineFlag} -interaction=nonstopmode -file-line-error -halt-on-error ${entry}
          ''}
        '';
      };

      buildApp = pkgs.writeShellApplication {
        name = "${name}-build";
        runtimeInputs = [ pkgs.xdg-utils ];
        text = ''exec xdg-open ${pdf}/${name}.pdf'';
      };

      apps = {
        default = { type = "app"; program = "${devApp}/bin/${name}-dev"; };
        build = { type = "app"; program = "${buildApp}/bin/${name}-build"; };
      };
    in
    { inherit source pdf crate zip shell texEnv apps; };
in
{ inherit templates document toolkitFiles; }
