# Figures: the variants a document asks of each project, and the build of each variant (a standalone PDF).
#
#   figureBuild { name; template; projects; figures; } -> { unknownFigures; variants; compile; }
#   unknownFigures  problems with the document's `figures` argument (a project it does not have)
#   variants        p -> [ { key; fig; width; paramsTeX; } ]   one per figure of project p, plus the variants asked for
#   compile         p -> variant -> derivation holding <key>.pdf
# Widths and parameters are computed by figures.nix; this file only places them in a build.
{ pkgs, lib, toolkit, fonts, engines, extension }:
{ name, template, projects, figures }:
let
  t = template;
  flag = engines.flag t.engine;
  figs = import ./figures.nix { inherit lib; };

  byName = lib.listToAttrs (map (p: lib.nameValuePair p.name p) projects);
  unknownFigures = lib.concatLists (lib.mapAttrsToList
    (p: fs: if byName ? ${p} then [ ] else [ "project ${p} is not among this document's projects" ]) figures);

  # A width is a length or a fraction of figure.width.
  variants = p:
    let
      asked = figures.${p.name} or { };
      spec = key: v:
        let
          v' = if lib.isAttrs v then v else { width = v; };
          from = v'.from or key;
          where = "${name}: figure ${p.name}/${key}";
        in
        if p.figures ? ${from}
        then {
          inherit key;
          fig = p.figures.${from} // lib.optionalAttrs (v' ? options) { inherit (v') options; };
          width = figs.width where t.figure.width (v'.width or t.figure.width);
          paramsTeX = figs.paramsTeX where (v'.params or { });
        }
        else throw "doc: ${name}: figure ${p.name}/${from} is not a figure of ${p.name}";
    in
    lib.mapAttrsToList (key: _: spec key (asked.${key} or t.figure.width)) p.figures
    ++ lib.mapAttrsToList spec (lib.filterAttrs (k: _: !(p.figures ? ${k})) asked);

  figureEnv = engines.texEnv (ps: [ ps.latexmk ps.standalone ] ++ t.packages ps ++ t.figure.packages ps ++ lib.concatMap (p: p.packages ps) projects);
  # Paths in fonts.tex are relative here (\nixsciroot is empty), so the files sit where they say.
  figureFontsSetup = ''
    cp ${toolkit}/nixsci-fonts.sty .
    ${lib.optionalString ((t.figure.fonts or { }) != { }) ''
      cp ${pkgs.writeText "${name}-figure-fonts.tex" (fonts.fontsTex t.figure.fonts)} fonts.tex
      ${lib.concatMapStrings (f: "mkdir -p ${dirOf f.to}\ncp -L ${f.from} ${f.to}\n") (fonts.files t.figure.fonts)}
    ''}
  '';
  compile = p: v: pkgs.runCommand "${p.name}-${v.key}-${t.name}"
    { nativeBuildInputs = [ figureEnv ]; FORCE_SOURCE_DATE = "1"; } ''
    ${engines.home t.engine}
    mkdir -p data ${p.name}
    ${lib.concatMapStrings (d: "cp ${p.data.${d}.path} data/${d}${extension p.data.${d}.path}\n") v.fig.data}
    ${lib.concatStrings (lib.mapAttrsToList (k: e: "cp -RL --no-preserve=mode ${e.path} ${p.name}/${k}${extension e.path}\n") p.files)}
    ${lib.optionalString (p.style != null) "cp ${p.style} ${p.name}.sty"}
    cp ${v.fig.src} figure.tex
    ${figureFontsSetup}
    cat > wrapper.tex <<'EOF'
    \documentclass[border=0pt,${t.figure.size}]{standalone}
    ${t.figure.useTex}${lib.optionalString ((t.figure.fonts or { }) != { }) "\\usepackage{nixsci-fonts}\n"}${t.figure.preamble}
    \providecommand{\nixscipath}[1]{#1}
    ${lib.optionalString (p.style != null) "\\usepackage{${p.name}}"}
    \def\datadir{data}\def\figwidth{${v.width}}
    ${v.fig.options}
    ${v.paramsTeX}
    \begin{document}\input{figure}\end{document}
    EOF
    latexmk ${flag} -interaction=nonstopmode -halt-on-error wrapper.tex >/dev/null || { cat wrapper.log; exit 1; }
    mkdir $out
    cp wrapper.pdf $out/${v.key}.pdf
  '';
in
{ inherit unknownFigures variants compile; }
