# Templates: what a document is typeset with. The document class and its options, the packages,
# the fonts, the template's own style file and the medium (the toolkit mechanism set). doc.nix
# imports this file and re-exports template, templates, pkgNames, generatedLines and usepackages.
#
#   templateLib = import ./template.nix { inherit pkgs toolkit; };
#
# Packages: a list of TeX Live names as strings, or the old function ps: [ ... ]. Both normalise to
# the function. See pkgNames.
{ pkgs, toolkit }:
let
  inherit (pkgs) lib;
  fontLib = import ./fonts.nix { inherit lib; };

  # \usepackage names that TeX Live does not ship under that name as a package of their own, or
  # that the base distribution ships as a file of a package the set also names (tikz is in pgf,
  # longtable in tools). texliveSmall ships every one with no package list at all, so a name here
  # is accepted in a list and adds nothing to it. Each is checked by kpsewhich under texliveSmall in
  # doc/tests/test_template_nix.py. A name in neither the set nor this list is an error.
  baseNames = [
    "amssymb" "amsthm" "array" "bm" "calc" "color" "dcolumn" "expl3" "fontenc" "graphicx" "helvet"
    "ifpdf" "ifthen" "inputenc" "keyval" "lmodern" "longtable" "multicol" "pifont" "rotating"
    "scrlayer-scrpage" "subcaption" "tabularx" "textcomp" "tikz" "trig" "verbatim" "xparse" "xspace"
  ];

  # A TeX Live name, as an entry of the texlive set.
  isTexName = n: lib.isString n && builtins.match "[A-Za-z0-9_.+-]+" n != null;

  # packages: a list of TeX Live names, or ps: [ ... ]. Returns the function ps: [ ... ].
  # A list is checked here, so an unknown name fails when the list is used, naming the name.
  pkgNames = packages:
    if lib.isFunction packages then packages
    else if !(lib.isList packages) then throw "doc: packages must be a list of TeX Live names or a function ps: [ ... ], not ${builtins.typeOf packages}"
    else
      let
        unknown = lib.filter (n: !(isTexName n) || (!(pkgs.texlive ? ${n}) && !(lib.elem n baseNames))) packages;
      in
      if unknown != [ ]
      then throw "doc: not TeX Live package name(s): ${lib.concatMapStringsSep ", " (n: if lib.isString n then n else "<${builtins.typeOf n}>") unknown} (TeX Live names, as strings, e.g. \"booktabs\")"
      else ps: lib.filter (p: p != null) (map (n: if ps ? ${n} then ps.${n} else null) packages);

  # A \usepackage entry: "name", or { name = "options"; }. Normalised to { name; options; }.
  useEntry = u:
    if lib.isString u then useEntry { ${u} = ""; }
    else if lib.isAttrs u && builtins.length (builtins.attrNames u) == 1 then
      let n = lib.head (builtins.attrNames u); in
      if !(isTexName n) then throw "doc: use: ${n} is not a package name"
      else if !(lib.isString u.${n}) then throw "doc: use: the options of ${n} must be a string, not ${builtins.typeOf u.${n}}"
      else { name = n; options = u.${n}; }
    else throw "doc: use entries are a package name or { name = \"options\"; }, not ${builtins.typeOf u}";

  # The \usepackage lines for a list of normalised use entries.
  usepackages = us: lib.concatMapStrings (u:
    "\\usepackage${lib.optionalString (u.options != "") "[${u.options}]"}{${u.name}}\n") us;

  # The class: { name; options; } with options a list of strings, or null for none.
  classEntry = c:
    if c == null then null
    else if lib.isAttrs c && c ? name && lib.isString c.name then { name = c.name; options = c.options or [ ]; }
    else throw "doc: class must be { name = \"beamer\"; options = [ ... ]; }, not ${builtins.typeOf c}";

  template =
    { name
    , files ? null
    , packages ? (_: [ ])
    , engine ? "pdflatex"
    , meta ? { }
    , render ? (_: "")
    , figure ? { }
    , class ? null
    , use ? [ ]
    , style ? null
    , medium ? null
    , fonts ? { }
    }:
    let
      fonts' = lib.mapAttrs (_: fontLib.mk) fonts;
      use' = map useEntry use;
      figure' = { width = "12cm"; size = "10pt"; preamble = ""; packages = _: [ ]; use = [ ]; } // figure;
      figureUse = map useEntry figure'.use;
      # Packages a template's fonts and use lists name land in the document's list; figure.use lands
      # in the figures' list only.
      fontNames = fontLib.texPackages fonts';
      packages' = pkgNames packages;
      known = lib.filter (n: pkgs.texlive ? ${n});
      engineNames = { lualatex = [ "fontspec" "luatex85" ]; xelatex = [ "fontspec" ]; }.${engine} or [ ];
    in
    if fonts' != { } && engine == "pdflatex"
    then throw "doc: template ${name}: fonts need lualatex or xelatex, not pdflatex (set engine = \"lualatex\")"
    else {
      inherit name files engine meta render medium style;
      class = classEntry class;
      use = use';
      fonts = fonts';
      packages = ps: packages' ps ++ pkgNames (known (map (u: u.name) use') ++ fontNames ++ engineNames) ps;
      figure = figure' // {
        packages = ps: pkgNames figure'.packages ps ++ pkgNames (known (map (u: u.name) figureUse)) ps;
        use = figureUse;
        # The \usepackage lines of a figure's wrapper: the template's use, then the figure's own.
        useTex = usepackages (use' ++ figureUse);
      };
    };

  # The lines of nixsci/generated.tex that name the template and its projects, one macro per line.
  # Only the lines a template or project has: a plain document gets none of them. Order: class,
  # medium, use, style, project, bib.
  generatedLines = { template, projects }:
    let
      t = template;
      opt = s: v: lib.optionalString (v != null) "\\nixsci@${s}{${v}}\n";
      cls = lib.optionalString (t.class != null)
        "\\nixsci@class{${t.class.name}}{${lib.concatStringsSep "," t.class.options}}\n";
      uses = lib.concatMapStrings (u: "\\nixsci@use{${u.name}}{${u.options}}\n") t.use;
      sty = lib.optionalString (t.style != null) (opt "style" (lib.removeSuffix ".sty" (baseNameOf t.style)));
      proj = lib.concatMapStrings (p: opt "project" (if p.style != null then p.name else null)) projects;
      bib = lib.concatMapStrings (p: opt "bib" (if p.bib != null then p.name else null)) projects;
    in
    cls + opt "medium" t.medium + uses + sty + proj + bib;

  # Built-in templates. plain: the document names its own class (no medium); the others carry
  # their medium, which selects the toolkit's \nixsci@style@<medium>.
  templates = {
    plain = template { name = "plain"; packages = ps: [ ps.latexmk ]; files = toolkit; meta = null; };
    paper = template { name = "paper"; files = toolkit; medium = "paper"; packages = ps: with ps; [ latexmk biblatex biber cleveref siunitx pgfplots enumitem ]; };
    # natbib ships bibentry.sty in nixpkgs, which the slides style's \citehere reads.
    slides = template { name = "slides"; files = toolkit; medium = "slides"; packages = ps: with ps; [ latexmk beamer pgfplots siunitx natbib ]; };
    poster = template { name = "poster"; files = toolkit; medium = "poster"; packages = ps: with ps; [ latexmk tikzposter a0poster xstring pgfplots siunitx ]; };
  };
in
{
  inherit template templates pkgNames generatedLines usepackages baseNames;
}
