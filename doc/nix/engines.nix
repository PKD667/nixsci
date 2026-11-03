# What a template's TeX engine implies, and the TeX Live environment a build runs in.
#
#   engines.flag "lualatex"  -> "-lualatex"       the latexmk flag
#   engines.home "lualatex"  -> shell to run first the font cache a font-aware engine writes needs a HOME
#   engines.name "lualatex"  -> "LuaLaTeX"        for the README; null for pdflatex
#   engines.texEnv (ps: [ ... ])                  texliveSmall with those packages
{ pkgs, lib }:
{
  flag = engine:
    { pdflatex = "-pdf"; lualatex = "-lualatex"; xelatex = "-xelatex"; }.${engine}
      or (throw "doc: engine must be pdflatex, lualatex or xelatex, not ${engine}");

  # A font-aware engine builds a font cache on first use and needs a writable HOME for it
  # (luaotfload ignores TEXMFVAR and TEXMFCACHE; measured).
  home = engine: lib.optionalString (engine != "pdflatex") ''export HOME="$TMPDIR"'';

  name = engine: { lualatex = "LuaLaTeX"; xelatex = "XeLaTeX"; }.${engine} or null;

  texEnv = packages: pkgs.texliveSmall.withPackages packages;
}
