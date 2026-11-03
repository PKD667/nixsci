# nixsci doc: a document is a function of the words you wrote, the projects it reads, and a
# template. Four values, each a Nix function:
#
#   doc = nixsci.lib.doc { inherit pkgs; };
#
#   nerve = doc.project {                                # what several documents share
#     name = "nerve";                                    # one namespace: nerve.sty, nerve/..., nerve.bib
#     style = ./nerve/nerve.sty;                         # \usepackage{nerve}
#     files.brain = ./nerve/assets/brain.pdf;            # \includegraphics{nerve/brain}
#     data.band = doc.static ./nerve/data/band.tsv;      # copied by hand: untraced
#     figures.band = { src = ./nerve/figures/band.tex; data = [ "band" ]; };  # -> nerve/band.pdf
#     values.band = doc.output analysis "band.json";     # \val{nerve/band.median}
#     bib = ./nerve/references.bib;                      # \bibliography{nerve}
#   };
#
#   talk = doc.document {
#     name = "nerve-talk";
#     src = ./nerve/talk;  entry = "slides.tex";         # you keep main.tex; it names the class
#     template = doc.templates.slides;  meta = { ... };  # metadata is a Nix value the template checks
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
#
# This file is the entry point. The work is in modules next to it, one job each:
#   template.nix, fonts.nix, figures.nix   what a document is typeset with, and its figures' widths
#   provenance.nix, project.nix             a project, and what its inputs can be traced to
#   document.nix                            the wiring of a document's stages:
#     meta.nix        meta against the template's slots
#     figure-build.nix  each figure variant, compiled on its own
#     remeasure.nix   the lab analyses it reads and the plan to measure them again
#     tree.nix        the tree under nixsci/ and the source tree
#     latex.nix       the TeX environment, the command, the PDF
#     publish.nix     crate, zip, zipCheck, publish
#     dev.nix         the dev shell and the apps
#   workspace.nix                           a whole flake of documents found by directory convention
{ pkgs, crateTool, cliTool ? null }:
let
  inherit (pkgs) lib;
  toolkit = ../files;
  fonts = import ./fonts.nix { inherit lib; };
  templateLib = import ./template.nix { inherit pkgs toolkit; };
  provenance = import ./provenance.nix { inherit lib; };
  engines = import ./engines.nix { inherit pkgs lib; };
  inherit (templateLib) templates pkgNames generatedLines;

  extension = p: let m = builtins.match ".*/[^/]*\\.([A-Za-z0-9]+)" (toString p); in if m == null then "" else ".${lib.head m}";

  # The stages of a document, each a function of explicit inputs (document.nix wires them).
  modules = {
    checkMeta = import ./meta.nix { inherit lib; };
    figureBuild = import ./figure-build.nix { inherit pkgs lib toolkit fonts engines extension; };
    remeasure = import ./remeasure.nix { inherit pkgs lib provenance; };
    treeOf = import ./tree.nix { inherit pkgs lib toolkit fonts generatedLines extension; };
    latexOf = import ./latex.nix { inherit pkgs lib engines pkgNames; };
    publishOf = import ./publish.nix { inherit pkgs lib crateTool engines; };
    devOf = import ./dev.nix { inherit pkgs lib cliTool; };
  };
in
{
  inherit (templateLib) template templates pkgNames generatedLines usepackages;

  # A font: see fonts.nix. doc.font { family; dir? or tex?; faces?; fake? }.
  font = fonts.mk;

  # Files with their provenance: see provenance.nix.
  inherit (provenance) static output;

  # What several documents share: see project.nix.
  project = import ./project.nix { inherit lib provenance pkgNames; };

  # A document: see document.nix.
  document = import ./document.nix { inherit pkgs lib modules provenance templates; };

  inherit toolkit;
  inherit (import ./figures.nix { inherit lib; }) figuresIn;
}
