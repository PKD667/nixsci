# nixsci out

A PDF is a function of three things: the text you wrote, the files your analyses produced, and the TeX that compiled them. `out` makes that literal, for papers, slides and posters.

```sh
nix flake init -t github:PKD667/nixsci#paper     # or #slides, #poster
nix build                                        # the PDF
nix run                                          # latexmk -pvc on the checkout, opens the PDF
nix build .#source                               # ordinary LaTeX: send it to Overleaf or arXiv
nix build .#crate                                # source + PDF + receipt as an RO-Crate
nix build .#zip                                  # the same source as a zip, for Overleaf
nix develop                                      # same TeX as the build; then `latexmk -pvc -pdf main.tex`
nixsci out tex/                                  # WRITING.md budgets and deny-list, as warnings
```

```nix
out = nixsci.lib.out { inherit pkgs; };
doc = out.document {
  name = "nerve";
  template = out.templates.paper;            # paper | slides | poster, or your own
  src = ./tex;                               # what you wrote: main.tex, references.bib, diagrams
  include."gen/cost.pdf" = "${lab.analyses.cost}/cost.pdf";   # what a pipeline wrote
  assets.nerve = {                           # the library: a key is an attrpath
    macros = { path = ./tex/macros.sty; packages = ps: [ xcolor pgfplots ]; };
    cost = "${lab.analyses.cost}/cost.pdf";
  };
  use = [ "nerve.macros" "nerve.cost" ];
  engine = "lualatex";                       # pdflatex (default) | lualatex | xelatex
};
```

The engine is the one choice about how the text is typeset, and it is the document's: pdfLaTeX by
default, `lualatex` or `xelatex` for a document that loads a font pdfLaTeX cannot read (fontspec
and an OTF). The receipt records the command, so the crate says which engine produced the PDF.

Figures and numbers enter only through `include`. A pipeline writes `\newcommand{\medianSpikes}{3506}` into a `.tex` file; the text says `\medianSpikes`. A number typed by hand is the one the build cannot trace.

The library is that mechanism with a name. A key is an attrpath into `assets` and the tree path is the attrpath, so `nerve.cost` is `nerve/cost.pdf` in the tree and LaTeX sees an ordinary name: `\includegraphics{nerve/cost}`, `\usepackage{nerve/macros}`. A leaf is a file or a directory, in the repository or a derivation output, so a static figure and an `lab.analysis` result take the same slot. `use` names the keys a document reads; an unknown key fails evaluation and lists the known keys. A leaf's `packages` joins the TeX environment, because a shared `\newcommand` set is not separable from what it calls.

A template is `{ packages = texlive-set -> [pkg]; files = path or null; }`. `files` is copied to the root of the source, which is where a house style (`.sty`, logos) goes. Every template ships `nixsci.sty`, so a document writes `\usepackage{nixsci}`, one `\nixscistyle{slides}` for the medium (`paper` and `poster` later), and one `\nixsciproject{<name>}` per project flake, which loads the `<name>.sty` that project publishes as a library key. The toolkit holds mechanisms, not identity: `\nixscistyle{slides}` gives `\paperfig`, which typesets a figure source the paper also `\input`s, while the project's colours and names travel in its own file. A project style is named for the project, never `slides`, which TeX already ships.

The slides medium also carries citations. `\citehere{key}` sets the full entry of `key` at the foot of the slide, read from the `.bbl` by `bibentry`, so what a slide prints is the reference the paper prints and the two cannot drift. The document declares the file with `\nobibliography{...}` and the project's style names the BibTeX style with `\bibliographystyle{...}`; the toolkit loads natbib and bibentry, which nixpkgs' natbib ships, and drops the footnote mark because the full entry needs no pointer.

Template `files`, `src`, the used assets and `include` are merged into one tree. A path present in two of them fails the build, since the PDF would otherwise depend on merge order.

`nix run` edits the checkout, not the store: it symlinks the used assets into `.nixsci/out/assets`, puts that on `TEXINPUTS` (and on `BIBINPUTS` and `BSTINPUTS`, which bibtex reads instead of `TEXINPUTS`), runs `latexmk -pvc` and opens the PDF with `xdg-open`. `nix run .#build` opens the store build once. A document whose `src` is not a plain path passes `srcDir` for the dev loop.

Boundaries. The receipt records that `latexmk` succeeded in this TeX environment; the crate says which store path each included file came from. Neither says the analysis is right. `FORCE_SOURCE_DATE` is set, and `nix build --rebuild` reproduced the example PDF byte for byte (x86_64-linux, nixpkgs as locked, one run). `nixsci out` prints warnings and always exits 0.

Not built: syncing a document to a server, bibliography checks, a claim check that fails the build when a macro-backed number disagrees with its analysis.
